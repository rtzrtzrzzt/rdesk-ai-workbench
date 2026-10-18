"""Trusted local desktop execution; persistence, pairing and approval live outside."""

import asyncio
import hashlib
import io
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from workbench.contracts.errors import AppError
from workbench.modules.browser.service import private_path
from workbench.modules.desktop.dto import (
    DesktopAction,
    DesktopActionResult,
    DesktopCapability,
    DesktopCreate,
    DesktopFeature,
    DesktopGrant,
    DesktopLease,
    DesktopObservation,
    DesktopObserve,
    DesktopOwner,
    DesktopSession,
    DesktopStop,
)
from workbench.modules.desktop.native import choose_driver, failure


def now():
    return datetime.now(UTC)


def utc(value):
    if value.tzinfo is None:
        raise failure("desktop_expiry_invalid", "授权和租约必须含时区。", "validation")
    return value.astimezone(UTC)


@dataclass
class ManagedDesktop:
    owner: DesktopOwner
    grant: DesktopGrant
    view: DesktopSession
    observed: DesktopObservation | None = None
    native_state: object | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    native_task: asyncio.Task | None = None
    cache: dict = field(default_factory=dict)
    captures: int = 0


class DesktopService:
    def __init__(
        self,
        data_dir: Path,
        *,
        node_id="local-desktop",
        enabled=False,
        allow_control=False,
        assert_lease=None,
    ):
        self.root = Path(data_dir).absolute()
        self.node_id = node_id
        self.enabled = enabled
        self.allow_control = allow_control
        self.assert_lease = assert_lease
        self.driver = None
        self.driver_error = None
        self.driver_init_lock = threading.Lock()
        self.sessions: dict[str, ManagedDesktop] = {}
        self.requests = {}
        self.control_session = None
        self.capture_bytes = 0
        self.create_lock = asyncio.Lock()
        self.capture_verified = False

    def emergency_stop(self):
        marker = private_path(self.root, "desktop/STOP")
        marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(marker.parent, 0o700)
        descriptor = os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as out:
            out.write("stop\n")
            out.flush()
            os.fsync(out.fileno())
        for session in self.sessions.values():
            session.cancelled.set()
            session.observed = None
            session.view.generation += 1

    def _emergency_pending(self):
        return (self.root / "desktop" / "STOP").exists()

    def _driver(self):
        with self.driver_init_lock:
            if self.driver is None:
                try:
                    self.driver = choose_driver()
                except AppError as exc:
                    self.driver_error = exc
                    raise
        return self.driver

    async def probe(self) -> DesktopCapability:
        kind = platform.system()
        session_type = os.environ.get("XDG_SESSION_TYPE", "") if kind == "Linux" else kind.lower()
        state = None
        driver = None
        issue = None
        try:
            driver = await asyncio.wait_for(asyncio.to_thread(self._driver), 3)
            state = await asyncio.wait_for(asyncio.to_thread(driver.snapshot), 3)
        except AppError as exc:
            issue = exc
        except Exception:
            issue = failure("desktop_probe_failed", "当前原生桌面状态读取失败；不会改用其他目标。")
        wayland = bool(issue and issue.code == "desktop_portal_required")
        capture = DesktopFeature(
            status="needs_permission"
            if wayland
            else "unavailable"
            if issue
            else "available"
            if self.capture_verified
            else "waiting_verification",
            reason=issue.message
            if issue
            else "已在本进程完成真实原生截图。"
            if self.capture_verified
            else "原生几何可读；捕获权限还需以实际截图核实。",
        )
        if kind == "Darwin" and driver and not getattr(driver, "screen_permission", False):
            capture = DesktopFeature(
                status="needs_permission", reason="macOS屏幕录制权限尚未授予实际进程。"
            )
        input_status = (
            "needs_permission"
            if wayland or not self.allow_control or self.assert_lease is None
            else "unavailable"
            if issue
            else "waiting_verification"
            if driver and not driver.platform_verified
            else "available"
        )
        if driver and not driver.input_supported:
            input_status = "unsupported"
        return DesktopCapability(
            node_id=self.node_id,
            platform=kind,
            architecture=platform.machine(),
            session_type=session_type or "unknown",
            driver=driver.name if driver else "portal-required" if wayland else "unavailable",
            capture=capture,
            input=DesktopFeature(
                status=input_status,
                reason="输入需要显式授权、活跃持久租约及实际平台验证；不会根据DISPLAY自动启用。",
            ),
            window_identity=DesktopFeature(
                status="available"
                if state and state.window_id and state.window_pid
                else "unavailable",
                reason="仅当前前台窗口与原生PID；不伪造无障碍树或后台窗口控制。",
            ),
            human_input_detection=DesktopFeature(
                status="unsupported",
                reason="只能发现指针/持有键/前台变化，无法可靠发现全部人工输入；控制必须显式接管约定。",
            ),
            os_lock_state=state.locked if state else None,
            platform_verified=bool(driver and driver.platform_verified),
            limitations=[
                "无OS沙箱、无剪贴板/音视频/文件传输。",
                "坐标只绑定此次原始截图与窗口/显示器几何；不猜测缩放。",
                "持久归属、配对、授权、租约和Operation由可信主调用方管理。",
                "Windows/macOS无当前实机验收；Wayland必须Portal授权，不降级XWayland。",
            ],
        )

    def _get(self, owner, sid):
        session = self.sessions.get(str(sid))
        if session is None or session.owner != owner:
            raise failure(
                "desktop_session_not_found", "桌面会话不存在或不属于当前身份。", "permission"
            )
        if self._emergency_pending():
            session.cancelled.set()
            session.observed = None
            session.view.status = "stopping"
            session.view.generation += 1
            raise failure(
                "desktop_emergency_stop",
                "本机紧急停止标记已生效；需要人工核对后显式重新启用。",
                "cancelled",
            )
        if session.view.status != "active":
            raise failure(
                "desktop_session_stopped", "会话已停止/接管，必须重新授权与观察。", "cancelled"
            )
        if session.view.expires_at <= now() or utc(session.grant.expires_at) <= now():
            session.cancelled.set()
            session.view.status = "expired"
            session.view.generation += 1
            raise failure("desktop_session_expired", "桌面会话或授权已过期。", "permission")
        return session

    async def create(
        self, owner: DesktopOwner, command: DesktopCreate, grant: DesktopGrant
    ) -> DesktopSession:
        async with self.create_lock:
            return await self._create(owner, command, grant)

    async def _create(self, owner, command, grant):
        if self._emergency_pending():
            raise failure(
                "desktop_emergency_stop", "本机紧急停止仍有效，不能新建桌面会话。", "cancelled"
            )
        if not self.enabled or not grant.capture:
            raise failure("desktop_not_enabled", "桌面观察尚未显式授权。", "permission")
        if utc(grant.expires_at) <= now():
            raise failure("desktop_grant_expired", "桌面授权已过期。", "permission")
        signature = hashlib.sha256(
            (owner.model_dump_json() + command.model_dump_json() + grant.model_dump_json()).encode()
        ).hexdigest()
        key = str(command.request_id)
        if key in self.requests:
            old, sid = self.requests[key]
            if old != signature:
                raise failure("desktop_request_conflict", "相同请求ID参数不同。", "conflict")
            return self.sessions[sid].view
        if len(self.sessions) >= 128:
            raise failure("desktop_session_budget", "本companion会话历史预算已满。", "budget")
        if sum(s.view.status == "active" for s in self.sessions.values()) >= 4:
            raise failure("desktop_session_budget", "活动桌面会话已达到4个上限。", "budget")
        driver = await asyncio.wait_for(asyncio.to_thread(self._driver), 3)
        state = await asyncio.wait_for(asyncio.to_thread(driver.snapshot), 3)
        if command.target_window_id != grant.target_window_id:
            raise failure("desktop_grant_scope", "会话窗口与用户授权目标不同。", "permission")
        if command.target_window_id and (
            state.window_id != command.target_window_id
            or state.window_pid != grant.target_window_pid
        ):
            raise failure(
                "desktop_target_changed", "被授权窗口/PID已变化，不能切换到当前窗口。", "conflict"
            )
        if command.mode == "control":
            if not self.allow_control or not grant.control or self.assert_lease is None:
                raise failure(
                    "desktop_control_not_authorized",
                    "控制需要主入口可信授权与持久租约验证器。",
                    "permission",
                )
            if not command.manual_takeover_ack:
                raise failure(
                    "desktop_takeover_ack_required",
                    "此驱动不能检测全部人工输入，请先明确显式接管约定。",
                    "permission",
                )
            if (
                not command.target_window_id
                or not grant.target_window_pid
                or state.locked is not False
                or not driver.input_supported
            ):
                raise failure(
                    "desktop_control_unavailable",
                    "控制需要已知解锁状态、精确前台窗口/PID与已实现原生驱动。",
                    "permission",
                )
            current = self.sessions.get(self.control_session)
            if current and current.view.status in {"active", "stopping"}:
                raise failure("desktop_write_busy", "当前本机桌面已有控制会话。", "conflict")
        sid = str(uuid4())
        created = now()
        view = DesktopSession(
            session_id=sid,
            node_id=self.node_id,
            generation=1,
            mode=command.mode,
            status="active",
            target_window_id=command.target_window_id,
            created_at=created,
            expires_at=min(created + timedelta(seconds=command.ttl_seconds), utc(grant.expires_at)),
        )
        self.sessions[sid] = ManagedDesktop(owner, grant, view)
        self.requests[key] = (signature, sid)
        if command.mode == "control":
            self.control_session = sid
        return view

    def _capture(self, session, command):
        driver = self._driver()
        before = driver.snapshot()
        if session.grant.target_window_id and (
            before.window_id != session.grant.target_window_id
            or before.window_pid != session.grant.target_window_pid
        ):
            raise failure(
                "desktop_target_changed", "授权窗口已不在前台；不会激活或切换窗口。", "conflict"
            )
        if before.locked is True:
            raise failure("desktop_locked", "当前桌面已锁定，不捕获受保护会话。", "permission")
        b = before.bounds
        if b.width < 1 or b.height < 1 or b.width * b.height > 32_000_000:
            raise failure("desktop_capture_budget", "原生截图几何超过3200万像素预算。", "budget")
        image = driver.capture(before)
        if image.width * image.height > 32_000_000:
            raise failure("desktop_capture_budget", "实际截图超过3200万像素预算。", "budget")
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
        content = encoded.getvalue()
        if len(content) > 16 * 1024 * 1024:
            raise failure("desktop_capture_budget", "PNG截图超过16MiB预算。", "budget")
        after = driver.snapshot()
        if before.geometry() != after.geometry() or before.locked != after.locked:
            raise failure(
                "desktop_observation_changed",
                "截图期间窗口/显示器/锁屏状态改变，请重新观察。",
                "conflict",
            )
        return before, image.size, content

    def _output(self, session, command):
        if command.output_dir is None:
            runtime = private_path(self.root, "desktop", create=True)
            os.chmod(runtime, 0o700)
            return private_path(
                self.root,
                f"desktop/observations/{session.owner.user_id}/{session.view.session_id}",
                create=True,
            )
        path = Path(command.output_dir)
        base = private_path(self.root, f"workspaces/{session.owner.workspace_id}/staging")
        if not path.is_absolute() or not path.is_relative_to(base):
            raise failure(
                "desktop_output_forbidden", "截图只允许当前工作区可信worker staging。", "permission"
            )
        suffix = path.relative_to(base).parts
        if len(suffix) != 2 or (session.owner.run_id and suffix[0] != str(session.owner.run_id)):
            raise failure("desktop_output_forbidden", "run/attempt截图目标无效。", "permission")
        try:
            for part in suffix:
                UUID(part)
        except ValueError:
            raise failure(
                "desktop_output_forbidden", "run/attempt标识无效。", "permission"
            ) from None
        path = private_path(self.root, str(path.relative_to(self.root)))
        if not path.is_dir():
            raise failure("desktop_output_forbidden", "worker staging尚不存在。", "permission")
        return path

    async def observe(
        self, owner, sid, command: DesktopObserve | None = None
    ) -> DesktopObservation:
        session = self._get(owner, sid)
        command = command or DesktopObserve()
        if session.native_task and not session.native_task.done():
            raise failure("desktop_busy", "先前本机调用仍未结束。", "conflict")
        if session.lock.locked():
            raise failure("desktop_busy", "桌面会话正在执行其他调用。", "conflict")
        if session.captures >= 16 or self.capture_bytes >= 128 * 1024 * 1024:
            raise failure(
                "desktop_capture_budget", "本会话16张/companion128MiB截图预算已满。", "budget"
            )
        async with session.lock:
            task = asyncio.create_task(asyncio.to_thread(self._capture, session, command))
            session.native_task = task
            done, _ = await asyncio.wait([task], timeout=5)
            if not done:
                task.add_done_callback(
                    lambda completed: completed.exception() if not completed.cancelled() else None
                )
                raise failure(
                    "desktop_capture_pending", "本机捕获超时，禁止同会话重复启动，待原调用结束。"
                )
            try:
                state, size, content = task.result()
            finally:
                session.native_task = None
            self._get(owner, sid)
            if self.capture_bytes + len(content) > 128 * 1024 * 1024:
                raise failure("desktop_capture_budget", "截图总量预算已满。", "budget")
            if session.native_state and session.native_state.geometry() != state.geometry():
                session.view.generation += 1
            target = self._output(session, command) / f"desktop-{uuid4()}.png"
            with target.open("xb") as out:
                os.chmod(target, 0o600)
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            self.capture_bytes += len(content)
            session.captures += 1
            self.capture_verified = True
            b = state.bounds
            observation = DesktopObservation(
                node_id=self.node_id,
                session_id=session.view.session_id,
                observation_id=str(uuid4()),
                generation=session.view.generation,
                geometry_id=state.geometry(),
                captured_at=now(),
                displays=list(state.displays),
                window_id=state.window_id,
                window_pid=state.window_pid,
                bounds=b,
                screenshot_path=str(target),
                content_hash=hashlib.sha256(content).hexdigest(),
                byte_size=len(content),
                image_width=size[0],
                image_height=size[1],
                logical_width=b.width,
                logical_height=b.height,
                pixel_ratio_x=size[0] / b.width,
                pixel_ratio_y=size[1] / b.height,
                locked=state.locked,
            )
            session.observed = observation
            session.native_state = state
            return observation

    async def action(
        self, owner, command: DesktopAction, lease: DesktopLease
    ) -> DesktopActionResult:
        session = self._get(owner, str(command.session_id))
        driver = self._driver()
        if session.view.mode != "control" or not self.allow_control or self.assert_lease is None:
            raise failure(
                "desktop_control_not_authorized", "观察会话不能升级为控制。", "permission"
            )
        request_id = str(command.request_id)
        signature = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
        if request_id in session.cache:
            previous, result = session.cache[request_id]
            if previous != signature:
                raise failure("desktop_request_conflict", "相同动作ID参数不同。", "conflict")
            return result
        if session.lock.locked():
            raise failure("desktop_busy", "同一桌面只允许一个本机写入者。", "conflict")
        if len(session.cache) >= 128:
            raise failure("desktop_action_budget", "会话动作预算已满。", "budget")
        async with session.lock:
            started = now()
            invoked = threading.Event()
            session.cancelled.clear()
            obs = session.observed
            state = session.native_state
            session.observed = None
            deadline = time.monotonic() + 3
            expected_pointer = [state.pointer if state else None]

            def guard(pointer=None, *, effect=False):
                if session.cancelled.is_set() or time.monotonic() > deadline:
                    raise failure(
                        "desktop_action_cancelled", "原生动作已停止或达到3秒执行预算。", "cancelled"
                    )
                self._get(owner, str(command.session_id))
                if utc(lease.expires_at) <= now() or lease.grant_id != session.grant.grant_id:
                    raise failure("desktop_lease_expired", "桌面租约已失效。", "permission")
                self.assert_lease(owner, session.view, lease)
                if pointer is not None:
                    expected_pointer[0] = pointer
                current = driver.snapshot()
                if current.locked is not False or current.geometry() != state.geometry():
                    raise failure(
                        "desktop_observation_stale",
                        "前台窗口/PID、桌面会话或显示器几何已变化。",
                        "conflict",
                    )
                if current.pointer != expected_pointer[0] or current.held_input != "clear":
                    session.view.status = "taken_over"
                    session.view.generation += 1
                    session.cancelled.set()
                    raise failure(
                        "desktop_human_interference",
                        "检测到指针/持有键变化；停止控制并要求显式接管。",
                        "cancelled",
                    )
                if time.monotonic() > deadline or session.cancelled.is_set():
                    raise failure(
                        "desktop_action_cancelled", "桌面输入在调用前已超时/停止。", "cancelled"
                    )
                if effect:
                    invoked.set()

            def execute():
                if (
                    obs is None
                    or state is None
                    or obs.observation_id != str(command.last_observation)
                    or obs.generation != command.generation
                    or obs.geometry_id != command.geometry_id
                    or command.target_window_id != state.window_id
                ):
                    raise failure(
                        "desktop_observation_stale",
                        "动作必须绑定当前原始截图和几何代数。",
                        "conflict",
                    )
                if (
                    command.target_window_id != session.grant.target_window_id
                    or state.window_pid != session.grant.target_window_pid
                ):
                    raise failure(
                        "desktop_target_changed", "动作不属于被授权窗口/PID。", "permission"
                    )
                guard()
                logical = command.model_copy()
                if command.x is not None:
                    if command.x >= obs.image_width or command.y >= obs.image_height:
                        raise failure(
                            "desktop_coordinates_invalid", "坐标超出原始截图。", "validation"
                        )
                    logical.x = int(command.x / obs.pixel_ratio_x)
                    logical.y = int(command.y / obs.pixel_ratio_y)
                driver.perform(logical, state, session.cancelled, guard)

            task = asyncio.create_task(asyncio.to_thread(execute))
            session.native_task = task
            status = "not_executed"
            code = None
            message = "原生动作预检未通过。"
            try:
                done, _ = await asyncio.wait([task], timeout=3.2)
                if not done:
                    session.cancelled.set()
                    session.view.status = "stopping"
                    raise failure(
                        "desktop_native_call_pending",
                        "本机调用未及时返回，已禁止后续输入；结果需核对。",
                        "outcome_unknown",
                    )
                task.result()
                status = "executed"
                message = "原生输入调用已返回；应用业务结果未经确认。"
            except asyncio.CancelledError:
                session.cancelled.set()
                session.view.status = "stopping"
                status = "outcome_unknown" if invoked.is_set() else "not_executed"
                code = "desktop_action_interrupted"
                message = "已停止后续输入，已发生效果需核对。"
            except Exception as exc:
                status = "outcome_unknown" if invoked.is_set() else "not_executed"
                code = exc.code if isinstance(exc, AppError) else "desktop_native_failed"
                message = exc.message if isinstance(exc, AppError) else "本机驱动失败，未重放输入。"
            finally:
                session.observed = None
                if task.done():
                    session.native_task = None
                else:
                    task.add_done_callback(
                        lambda completed: (
                            completed.exception() if not completed.cancelled() else None
                        )
                    )
            result = DesktopActionResult(
                request_id=request_id,
                session_id=session.view.session_id,
                execution_status=status,
                started_at=started,
                finished_at=now(),
                error_code=code,
                message=message,
            )
            session.cache[request_id] = (signature, result)
            return result

    async def stop(self, owner, sid, *, takeover=False) -> DesktopStop:
        session = self.sessions.get(str(sid))
        if session is None or session.owner != owner:
            raise failure(
                "desktop_session_not_found", "桌面会话不存在或不属于当前身份。", "permission"
            )
        session.cancelled.set()
        session.observed = None
        session.view.generation += 1
        pending = bool(session.native_task and not session.native_task.done())
        released = not pending and bool(getattr(self.driver, "inputs_released", True))
        if not pending and not released and self.driver and hasattr(self.driver, "release_owned"):
            try:
                released = await asyncio.wait_for(asyncio.to_thread(self.driver.release_owned), 2)
            except Exception:
                released = False
        session.view.status = (
            "stopping" if pending or not released else "taken_over" if takeover else "stopped"
        )
        if self.control_session == str(sid) and not pending and released:
            self.control_session = None
        return DesktopStop(
            session_id=str(sid),
            status=session.view.status,
            generation=session.view.generation,
            pending_native_call=pending,
            owned_inputs_released=released,
        )

    async def forget(self, owner: DesktopOwner, sid: str, *, durable_closed=False):
        from workbench.modules.automation.forget import (
            SessionForgotten,
            remove_session_files,
        )

        sid = str(UUID(str(sid)))
        async with self.create_lock:
            session = self.sessions.get(sid)
            if session is not None:
                if session.owner != owner:
                    raise failure("desktop_session_not_found", "桌面会话归属不匹配。", "permission")
                if (
                    session.view.status not in {"stopped", "taken_over"}
                    or session.lock.locked()
                    or session.native_task
                    and not session.native_task.done()
                    or self.control_session == sid
                ):
                    raise failure("desktop_forget_not_ready", "桌面会话尚未关闭，不能清除。")
                # Stop has already released this session's inputs. A different
                # active control session may now own the shared driver.
                async with session.lock:
                    remove_session_files(self.root, owner, sid, "desktop")
                    session.observed = None
                    session.native_state = None
                    session.native_task = None
                    session.cache.clear()
                    self.sessions.pop(sid)
            else:
                # GovernedDesktop validates the still-present durable row first.
                if not durable_closed:
                    raise failure("desktop_session_not_found", "桌面会话归属未确认。", "permission")
                remove_session_files(self.root, owner, sid, "desktop")
            self.requests = {key: value for key, value in self.requests.items() if value[1] != sid}
        return SessionForgotten(session_id=sid)
