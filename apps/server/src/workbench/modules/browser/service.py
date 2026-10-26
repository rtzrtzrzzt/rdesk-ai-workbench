"""One owned Chrome per session. No DB, application credentials, or tool execution."""

import asyncio
import hashlib
import importlib.metadata
import os
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from workbench.contracts.errors import AppError
from workbench.modules.browser import downloads
from workbench.modules.browser.dto import (
    BrowserAction,
    BrowserActionResult,
    BrowserCapability,
    BrowserCreate,
    BrowserDownload,
    BrowserDownloaded,
    BrowserElement,
    BrowserObservation,
    BrowserObserve,
    BrowserOwner,
    BrowserSession,
    BrowserStop,
)
from workbench.modules.browser.policy import NetworkPolicy
from workbench.modules.browser.proxy import SessionProxy

# Fixed internal DOM reader. No caller-provided JS, selector, expression or handler.
# Its closure is held by a JSHandle, not an exposed window property. A mutation
# invalidates every previous reference, including in-place SPA DOM changes.
DOM_READER = """() => {
  let revision = 0;
  const observer = new MutationObserver(() => revision++);
  observer.observe(document, {subtree:true,childList:true,attributes:true,characterData:true});
  return {
    revision: () => revision,
    snapshot: () => {
      const candidates = Array.from(document.querySelectorAll(
        'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],'
        + '[contenteditable=true],[tabindex]')).slice(0, 2000);
      const elements = candidates.filter(e => {
        const r = e.getBoundingClientRect();
        const s = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
      }).slice(0, 200);
      const text = document.body ? document.body.innerText : '';
      return {revision, title:document.title.slice(0,500), text:text.slice(0,32000),
              truncated:text.length>32000 || candidates.length>200, elements};
    }
  };
}"""
ELEMENT_INFO = """e => ({tag:e.tagName.toLowerCase(),
 role:(e.getAttribute('role') || ({A:'link',BUTTON:'button',INPUT:'textbox',TEXTAREA:'textbox',
 SELECT:'combobox'})[e.tagName] || 'element').slice(0,64),
 name:(e.getAttribute('aria-label') || e.getAttribute('alt') || e.getAttribute('title')
       || (e.labels && Array.from(e.labels).map(x=>x.innerText).join(' '))
       || e.innerText || '').slice(0,240),
 input_type:e.tagName==='INPUT' ? e.type : null,
 href:e.tagName==='A' && typeof e.href==='string' ? e.href.slice(0,4097) : null})"""


def now():
    return datetime.now(UTC)


def error(code, message, status=409, kind="conflict"):
    return AppError(code, message, status, kind, False)


def digest(command) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def private_path(root: Path, relative: str, *, create=False) -> Path:
    """Only caller-owned regular directories, no symlink traversal."""
    if root.is_symlink():
        raise error("browser_path_forbidden", "受管浏览器目录不能是符号链接。")
    probe = root
    for part in Path(relative).parts:
        if part in {"..", "/"}:
            raise error("browser_path_forbidden", "受管浏览器路径无效。")
        probe /= part
        if probe.is_symlink():
            raise error("browser_path_forbidden", "受管浏览器路径不能经过符号链接。")
    target = probe.resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise error("browser_path_forbidden", "受管浏览器路径越界。")
    if create:
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


@dataclass
class ManagedSession:
    owner: BrowserOwner
    view: BrowserSession
    context: object
    page: object
    browser: object
    proxy: SessionProxy
    policy: NetworkPolicy
    directory: Path
    temporary_directory: Path
    timeout: int
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stop_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None
    observation: BrowserObservation | None = None
    reader: object | None = None
    revision: int | None = None
    refs: dict = field(default_factory=dict)
    link_hrefs: dict[str, str] = field(default_factory=dict)
    download_task: asyncio.Task | None = None
    navigation: int = 0
    observed_navigation: int = -1
    stopped: bool = False
    cache: dict = field(default_factory=dict)


class BrowserService:
    def __init__(
        self,
        data_dir: Path,
        *,
        enabled=False,
        trusted_local_origins=(),
        executable_path="/usr/bin/google-chrome",
        max_sessions=4,
    ):
        self.root = Path(data_dir).absolute()
        self.enabled = enabled
        self.trusted_local = {NetworkPolicy._exact(value) for value in trusted_local_origins}
        self.executable_path = executable_path
        self.max_sessions = min(max(max_sessions, 1), 8)
        self.sessions: dict[str, ManagedSession] = {}
        self.creates: dict[str, tuple[str, str, str]] = {}
        self.playwright = None
        self.browser_version = None
        self.launch_failed = False
        self.create_lock = asyncio.Lock()
        self.reaper = None

    async def start(self):
        # Optional dependency is imported only in the opt-in companion process.
        if self.enabled:
            try:
                from playwright.async_api import async_playwright

                self.playwright = await async_playwright().start()
            except (ImportError, OSError):
                raise error(
                    "browser_dependency_unavailable", "Playwright 不可用。", 503, "dependency"
                ) from None
        self.reaper = asyncio.create_task(self._reap())

    async def close(self):
        if self.reaper:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
        for session in tuple(self.sessions.values()):
            await self._stop(session)
        if self.playwright:
            await self.playwright.stop()

    def capability(self) -> BrowserCapability:
        try:
            version = importlib.metadata.version("playwright")
        except importlib.metadata.PackageNotFoundError:
            version = None
        available = (
            self.playwright is not None
            and Path(self.executable_path).is_file()
            and not self.launch_failed
        )
        return BrowserCapability(
            status="unconfigured"
            if not self.enabled
            else "available"
            if available
            else "unavailable",
            reason=(
                "会话未启用；需要用户网络范围授权。"
                if not self.enabled
                else "依赖已检测；会话启动和目标页面仍需实际验证。"
                if available
                else "最近一次 Chrome 受管上下文启动失败；未静默降级。"
                if self.launch_failed
                else "Playwright 或 Chrome 二进制不可用。"
            ),
            playwright_version=version,
            browser_version=self.browser_version,
            enabled=self.enabled,
            sessions=sum(not s.stopped for s in self.sessions.values()),
        )

    async def _reap(self):
        while True:
            await asyncio.sleep(5)
            for session in tuple(self.sessions.values()):
                if session.view.status == "stopping" or (
                    not session.stopped and session.view.expires_at <= now()
                ):
                    if session.view.status != "stopping":
                        session.view.status = "expired"
                    await self._stop(session)

    def _session(self, owner: BrowserOwner, session_id: str) -> ManagedSession:
        session = self.sessions.get(str(session_id))
        if session is None or session.owner != owner:
            raise error("browser_session_not_found", "浏览器会话不存在或不属于当前执行身份。", 404)
        if session.stopped or session.view.expires_at <= now():
            raise error(
                "browser_session_stopped", "浏览器会话已停止或过期；不会重连后重放动作。", 410
            )
        return session

    async def create(self, owner: BrowserOwner, command: BrowserCreate) -> BrowserSession:
        if not self.enabled or not command.enabled or self.playwright is None:
            raise error(
                "browser_not_enabled", "浏览器尚未显式启用或缺少用户授权。", 403, "permission"
            )
        policy = NetworkPolicy(
            command.allowed_origins,
            command.local_origins,
            self.trusted_local,
            allow_public=command.allow_public_network,
        )
        async with self.create_lock:
            create_id = str(command.request_id)
            signature = digest(command)
            identity = owner.model_dump_json()
            if create_id in self.creates:
                previous, previous_owner, sid = self.creates[create_id]
                if previous != signature or previous_owner != identity:
                    raise error("browser_request_conflict", "同一请求 ID 的参数或身份不同。")
                return self.sessions[sid].view
            if sum(not s.stopped for s in self.sessions.values()) >= self.max_sessions:
                raise error("browser_capacity", "受管浏览器会话已达到并发上限。", 429, "budget")
            if len(self.sessions) >= 256:
                raise error(
                    "browser_history_capacity",
                    "当前 companion 会话历史已满，请结束会话后重启。",
                    429,
                    "budget",
                )
            sid = str(uuid4())
            directory = private_path(self.root, f"browser/profiles/{sid}", create=True)
            home = private_path(directory, "home", create=True)
            # Chrome creates its SingletonSocket below TMPDIR. The project's
            # Unicode absolute path exceeds Linux's Unix-socket pathname limit.
            # This random 0700 directory is exclusively this session's and is
            # removed on stop; profile/cookies still use our private project dir.
            tmp = Path(tempfile.mkdtemp(prefix="workbench-browser-", dir="/tmp"))
            proxy = SessionProxy(policy)
            address = await proxy.start()
            context = None
            try:
                context = await self.playwright.chromium.launch_persistent_context(
                    str(directory / "profile"),
                    executable_path=self.executable_path,
                    headless=True,
                    chromium_sandbox=True,
                    timeout=command.timeout_seconds * 1000,
                    proxy={"server": address, "bypass": "<-loopback>"},
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": str(home),
                        "TMPDIR": str(tmp),
                        "LANG": "C.UTF-8",
                    },
                    accept_downloads=False,
                    service_workers="block",
                    permissions=[],
                    viewport={"width": 1280, "height": 900},
                    args=[
                        "--disable-quic",
                        "--disable-extensions",
                        "--disable-background-networking",
                        "--dns-prefetch-disable",
                        "--disable-features=WebRtcAllowInputVolumeAdjustment",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                        "--proxy-bypass-list=<-loopback>",
                    ],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                browser = context.browser
                self.browser_version = browser.version if browser else None
                view = BrowserSession(
                    session_id=sid,
                    generation=1,
                    status="active",
                    created_at=now(),
                    expires_at=now() + timedelta(seconds=command.ttl_seconds),
                    allowed_origins=sorted(policy.public),
                    local_origins=sorted(policy.local),
                    allow_public_network=policy.allow_public,
                )
                session = ManagedSession(
                    owner,
                    view,
                    context,
                    page,
                    browser,
                    proxy,
                    policy,
                    directory,
                    tmp,
                    command.timeout_seconds,
                )
                await context.route("**/*", lambda route: self._route(session, route))
                await context.route_web_socket("**/*", lambda ws: ws.close())
                await context.add_init_script(
                    "Object.defineProperty(window,'RTCPeerConnection',{value:undefined});"
                    "Object.defineProperty(window,'webkitRTCPeerConnection',{value:undefined});"
                )
                context.on("page", lambda popup: asyncio.create_task(self._popup(session, popup)))
                page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
                page.on("filechooser", lambda chooser: None)
                page.on("download", lambda download: asyncio.create_task(download.cancel()))
                page.on("framenavigated", lambda frame: self._navigated(session, frame))
                if browser:
                    browser.on(
                        "disconnected",
                        lambda: (
                            asyncio.create_task(self._stop(session))
                            if not session.stopped
                            else None
                        ),
                    )
                self.sessions[sid] = session
                self.creates[create_id] = (signature, identity, sid)
                self.launch_failed = False
                return view
            except BaseException:
                self.launch_failed = True
                if context:
                    with suppress(Exception):
                        await context.close()
                await proxy.close()
                shutil.rmtree(directory, ignore_errors=True)
                shutil.rmtree(tmp, ignore_errors=True)
                raise error(
                    "browser_launch_failed",
                    "Chrome 受管上下文启动失败；未关闭沙箱或接管其他浏览器。",
                    503,
                    "dependency",
                ) from None

    @staticmethod
    def _navigated(session, frame):
        if frame == session.page.main_frame:
            session.navigation += 1
            session.view.generation += 1
            session.observation = None

    async def _popup(self, session, popup):
        if popup != session.page:
            await popup.close()

    async def _route(self, session, route):
        try:
            if session.stopped:
                raise error("browser_session_stopped", "会话已停止。")
            session.policy.validate(route.request.url)
            if route.request.resource_type in {"websocket", "other"}:
                raise error("browser_resource_forbidden", "此类网络资源未开放。")
            await route.continue_()
        except Exception:
            session.proxy.blocked += 1
            with suppress(Exception):
                await route.abort("blockedbyclient")

    async def _invalidate(self, session):
        session.observation = None
        handles = tuple(session.refs.values())
        session.refs.clear()
        session.link_hrefs.clear()
        if handles:
            with suppress(Exception):
                await asyncio.wait_for(
                    asyncio.gather(
                        *(handle.dispose() for handle in handles), return_exceptions=True
                    ),
                    2,
                )
        session.revision = None
        if session.reader:
            with suppress(Exception):
                await session.reader.dispose()
            session.reader = None

    def _output_path(self, session, output_dir, *, error_code="browser_screenshot_path"):
        output = Path(output_dir)
        if not output.is_absolute():
            raise error(error_code, "worker staging 必须是可信绝对路径。")
        base = private_path(self.root, f"workspaces/{session.owner.workspace_id}/staging")
        if not output.is_relative_to(base) or output == base:
            raise error(
                error_code,
                "输出只允许写入当前工作区 worker staging。",
                403,
                "permission",
            )
        output = private_path(self.root, str(output.relative_to(self.root)))
        suffix = output.relative_to(base).parts
        if len(suffix) != 2 or (session.owner.run_id and suffix[0] != str(session.owner.run_id)):
            raise error(error_code, "输出目标必须是可信 run/attempt staging。", 403, "permission")
        try:
            for part in suffix:
                UUID(part)
        except ValueError:
            raise error(error_code, "run/attempt staging 标识无效。") from None
        if not output.is_dir():
            raise error(error_code, "worker staging 目录尚未创建。")
        return output

    async def _observe(self, session, command: BrowserObserve) -> BrowserObservation:
        await self._invalidate(session)
        if session.page.url != "about:blank":
            session.policy.validate(session.page.url)
        nav = session.navigation
        reader = await session.page.evaluate_handle(DOM_READER)
        session.reader = reader
        snapshot = await reader.evaluate_handle("s => s.snapshot()")
        data = await snapshot.evaluate(
            "s => ({revision:s.revision,title:s.title,text:s.text,truncated:s.truncated})"
        )
        collection = await snapshot.get_property("elements")
        handles = await collection.get_properties()
        await collection.dispose()
        elements = []
        for index, handle in handles.items():
            element = handle.as_element()
            if element is None:
                continue
            ref = f"e{index}"
            session.refs[ref] = element
            info = await element.evaluate(ELEMENT_INFO)
            href = info.pop("href")
            allowed_href = None
            if href is not None:
                session.link_hrefs[ref] = href
                with suppress(AppError):
                    allowed_href = str(session.policy.validate(href).copy_with(fragment=None))
            elements.append(BrowserElement(ref=ref, href=allowed_href, **info))
        await snapshot.dispose()
        screenshot_path = None
        if command.screenshot:
            output = self._output_path(session, command.output_dir)
            content = await session.page.screenshot(
                type="png", full_page=False, caret="initial", timeout=session.timeout * 1000
            )
            if len(content) > 8 * 1024 * 1024:
                raise error("browser_screenshot_budget", "截图超过 8 MiB 预算。", 413, "budget")
            target = output / f"browser-{uuid4()}.png"
            with target.open("xb") as stream:
                os.chmod(target, 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            screenshot_path = str(target)
        current_revision = await reader.evaluate("s => s.revision()")
        if nav != session.navigation or current_revision != data["revision"]:
            if screenshot_path:
                Path(screenshot_path).unlink(missing_ok=True)
            raise error("browser_observation_changed", "页面在观察期间发生变化，请重新观察。")
        observation = BrowserObservation(
            session_id=session.view.session_id,
            observation_id=str(uuid4()),
            generation=session.view.generation,
            captured_at=now(),
            url=session.page.url,
            title=data["title"],
            text=data["text"],
            truncated=data["truncated"],
            elements=elements,
            screenshot_path=screenshot_path,
            screenshot_width=1280 if screenshot_path else None,
            screenshot_height=900 if screenshot_path else None,
            blocked_requests=session.proxy.blocked,
        )
        session.observation = observation
        session.observed_navigation = nav
        session.revision = current_revision
        return observation

    async def observe(self, owner, session_id, command: BrowserObserve) -> BrowserObservation:
        session = self._session(owner, session_id)
        if session.lock.locked():
            raise error("browser_busy", "浏览器会话正在执行其他动作。")
        async with session.lock:
            session.task = asyncio.current_task()
            try:
                async with asyncio.timeout(session.timeout):
                    return await self._observe(session, command)
            except asyncio.CancelledError:
                raise
            except AppError:
                raise
            except Exception:
                await self._invalidate(session)
                raise error(
                    "browser_observe_failed",
                    "页面观察失败；请确认会话仍可用后重新观察。",
                    502,
                    "dependency",
                ) from None
            finally:
                session.task = None

    async def _check_observation(self, session, command):
        previous = session.observation
        if (
            previous is None
            or previous.observation_id != str(command.last_observation)
            or session.observed_navigation != session.navigation
            or previous.url != session.page.url
            or session.reader is None
        ):
            raise error("browser_observation_stale", "观察已失效；请重新观察后再提出动作。")
        if await session.reader.evaluate("s => s.revision()") != session.revision:
            await self._invalidate(session)
            raise error("browser_observation_stale", "DOM 已发生变化；旧定位引用不能继续使用。")
        if session.page.url != "about:blank":
            session.policy.validate(session.page.url)
        if command.target_ref != "page":
            target = session.refs.get(command.target_ref)
            if target is None or not await target.evaluate("e => e.isConnected"):
                raise error("browser_ref_stale", "目标引用不属于当前观察或已从页面移除。")
            return target
        if command.kind in {"click", "type"}:
            raise error(
                "browser_ref_required", "点击和填写必须指定观察中的元素引用。", 422, "validation"
            )
        return None

    async def action(self, owner, command: BrowserAction) -> BrowserActionResult:
        session = self._session(owner, str(command.session_id))
        request_id = str(command.request_id)
        signature = digest(command)
        if request_id in session.cache:
            previous, result = session.cache[request_id]
            if previous != signature:
                raise error("browser_request_conflict", "同一动作请求 ID 的参数不同。")
            return result
        if session.lock.locked():
            raise error("browser_busy", "同一浏览器会话只允许一个写入者。")
        if len(session.cache) >= 128:
            raise error(
                "browser_action_capacity", "当前会话动作预算已满，请停止并新建会话。", 429, "budget"
            )
        async with session.lock:
            session.task = asyncio.current_task()
            started = now()
            invoked = False
            observation = None
            status = "not_executed"
            code = None
            message = "动作尚未执行。"
            try:
                async with asyncio.timeout(session.timeout):
                    target = await self._check_observation(session, command)
                    if command.kind == "navigate":
                        await session.policy.address(command.url)
                    if command.kind == "type" and await target.evaluate(
                        "e => e.tagName==='INPUT' && ['file','password'].includes(e.type)"
                    ):
                        raise error(
                            "browser_sensitive_input_unsupported",
                            "密码与文件上传需要独立受管授权计划；此入口未开放。",
                            403,
                            "permission",
                        )
                    # DNS/DOM awaits may outlive a permission change. Governed
                    # services re-read live authority at the effect boundary.
                    self._session(owner, str(command.session_id))
                    invoked = True
                    timeout = session.timeout * 1000
                    if command.kind == "navigate":
                        await session.page.goto(
                            command.url, wait_until="domcontentloaded", timeout=timeout
                        )
                        session.policy.validate(session.page.url)
                    elif command.kind == "click":
                        await target.click(timeout=timeout, no_wait_after=True)
                    elif command.kind == "type":
                        await target.fill(command.text, timeout=timeout)
                    elif command.kind == "key":
                        if target:
                            await target.press(command.key, timeout=timeout)
                        else:
                            await session.page.keyboard.press(command.key)
                    else:
                        if target:
                            await target.scroll_into_view_if_needed(timeout=timeout)
                            box = await target.bounding_box()
                            if box is None:
                                raise error("browser_ref_stale", "滚动目标已不可见。")
                            self._session(owner, str(command.session_id))
                            await session.page.mouse.move(
                                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                            )
                        self._session(owner, str(command.session_id))
                        await session.page.mouse.wheel(0, command.delta_y)
                    await self._invalidate(session)
                    status = "executed"
                    message = "浏览器动作已执行；网站业务结果尚未确认。"
                    with suppress(AppError):
                        observation = await self._observe(session, BrowserObserve())
            except asyncio.CancelledError:
                status = "outcome_unknown" if invoked else "not_executed"
                code = "browser_action_interrupted"
                message = "动作被停止；已发生的外部效果不能撤回，必须核对后再决定。"
            except Exception as exc:
                status = "outcome_unknown" if invoked else "not_executed"
                code = exc.code if isinstance(exc, AppError) else "browser_action_failed"
                message = (
                    "动作调用后未获得完整证据，结果不明；不会自动重放。"
                    if invoked
                    else exc.message
                    if isinstance(exc, AppError)
                    else "动作预检失败，未调用页面动作。"
                )
            finally:
                if observation is None:
                    await self._invalidate(session)
                session.task = None
            result = BrowserActionResult(
                request_id=request_id,
                session_id=session.view.session_id,
                execution_status=status,
                started_at=started,
                finished_at=now(),
                observation=observation,
                error_code=code,
                message=message,
                requires_observe=observation is None,
            )
            session.cache[request_id] = (signature, result)
            return result

    async def download(
        self, owner: BrowserOwner, session_id: str, command: BrowserDownload
    ) -> BrowserDownloaded:
        session = self._session(owner, session_id)
        request_id = str(command.request_id)
        signature = "download:" + digest(command)
        if request_id in session.cache:
            previous, result = session.cache[request_id]
            if previous != signature:
                raise error("browser_request_conflict", "同一请求 ID 的动作或参数不同。")
            if isinstance(result, AppError):
                raise result
            downloads.verify_candidate(result)
            return result
        if session.lock.locked():
            raise error("browser_busy", "同一浏览器会话只允许一个写入者。")
        if len(session.cache) >= 128:
            raise error("browser_action_capacity", "当前会话动作预算已满。", 429, "budget")
        async with session.lock:
            session.task = session.download_task = asyncio.current_task()
            started = now()
            try:
                async with asyncio.timeout(downloads.TIMEOUT):
                    await self._check_observation(session, command)
                    href = session.link_hrefs.get(command.target_ref)
                    if not href or len(href) > 4096:
                        raise error(
                            "browser_download_link_required",
                            "下载只支持当前观察中真实的 HTTP(S) 链接。",
                            422,
                            "validation",
                        )
                    source = str(session.policy.validate(href).copy_with(fragment=None))
                    output = self._output_path(
                        session, command.output_dir, error_code="browser_download_path"
                    )
                    generation = session.view.generation

                    async def still_current():
                        if session.stopped:
                            raise error(
                                "browser_session_stopped", "下载会话已停止。", 410, "resource"
                            )
                        current = await self._check_observation(session, command)
                        live_href = await current.evaluate(
                            "e => e.tagName==='A' && typeof e.href==='string' "
                            "? e.href.slice(0,4097) : null"
                        )
                        if session.view.generation != generation or live_href != href:
                            raise error(
                                "browser_observation_stale", "链接或页面已变化，请重新观察。"
                            )

                    metadata = await downloads.fetch(
                        session.policy,
                        session.proxy.address,
                        source,
                        output,
                        request_id,
                        still_current,
                    )
                    result = BrowserDownloaded(
                        request_id=request_id,
                        session_id=session.view.session_id,
                        generation=generation,
                        last_observation=str(command.last_observation),
                        status="downloaded",
                        started_at=started,
                        finished_at=now(),
                        **metadata,
                    )
                    session.cache[request_id] = (signature, result)
                    return result
            except asyncio.CancelledError:
                cancelled = error(
                    "browser_download_cancelled", "下载已取消，不会自动重放。", 409, "cancelled"
                )
                session.cache[request_id] = (signature, cancelled)
                if session.stopped:
                    raise cancelled from None
                raise
            except Exception as exc:
                failure = (
                    exc
                    if isinstance(exc, AppError)
                    else error(
                        "browser_download_timeout"
                        if isinstance(exc, TimeoutError)
                        else "browser_download_failed",
                        "下载未获完整候选证据；不会自动重试。",
                        504 if isinstance(exc, TimeoutError) else 502,
                        "transient",
                    )
                )
                session.cache[request_id] = (signature, failure)
                raise failure from None
            finally:
                session.task = session.download_task = None

    async def _stop(self, session) -> BrowserStop:
        async with session.stop_lock:
            return await self._stop_owned(session)

    async def _stop_owned(self, session) -> BrowserStop:
        interrupted = bool(session.task and not session.task.done())
        download_task = session.download_task
        session.stopped = True
        expired = session.view.status == "expired"
        session.view.status = "stopping"
        session.view.generation += 1
        if interrupted and session.task != asyncio.current_task():
            session.task.cancel()
        # Close network first; stop never queues behind the session action lock.
        with suppress(Exception):
            await asyncio.wait_for(session.proxy.close(), 3)
        # The download coroutine owns its client/descriptor and unlinks partial
        # candidates in finally. Stop must observe that cleanup before confirming.
        if download_task and download_task != asyncio.current_task() and not download_task.done():
            await asyncio.wait({download_task}, timeout=3)
        downloads_closed = download_task is None or download_task.done()
        context_closed = False
        with suppress(Exception):
            await asyncio.wait_for(session.context.close(), 5)
            context_closed = True
        if session.browser:
            with suppress(Exception):
                await asyncio.wait_for(session.browser.close(), 5)
        closed = downloads_closed and (
            not session.browser.is_connected() if session.browser is not None else context_closed
        )
        if closed:
            session.view.status = "expired" if expired else "stopped"
            await self._invalidate(session)
            shutil.rmtree(session.directory, ignore_errors=True)
            shutil.rmtree(session.temporary_directory, ignore_errors=True)
            session.cache.clear()
        return BrowserStop(
            session_id=session.view.session_id,
            status="stopped" if closed else "stopping",
            interrupted_action=interrupted,
            owned_resources_closed=closed,
        )

    async def stop(self, owner, session_id) -> BrowserStop:
        session = self.sessions.get(str(session_id))
        if session is None or session.owner != owner:
            raise error("browser_session_not_found", "浏览器会话不存在或不属于当前身份。", 404)
        if session.stopped and session.view.status != "stopping":
            return BrowserStop(
                session_id=session.view.session_id,
                status="stopped",
                interrupted_action=False,
                owned_resources_closed=True,
            )
        return await self._stop(session)

    async def forget(self, owner: BrowserOwner, session_id: str, *, durable_closed=False):
        from workbench.infrastructure.vault.private import private_directory
        from workbench.modules.automation.forget import (
            SessionForgotten,
            remove_private_directory,
            remove_session_files,
        )

        sid = str(UUID(str(session_id)))
        async with self.create_lock:
            session = self.sessions.get(sid)
            if session is not None:
                if session.owner != owner:
                    raise error("browser_session_not_found", "浏览器会话归属不匹配。", 404)
                async with session.stop_lock:
                    if (
                        not session.stopped
                        or session.view.status not in {"stopped", "expired"}
                        or session.lock.locked()
                        or session.task
                        and not session.task.done()
                        or session.download_task
                        and not session.download_task.done()
                        or session.browser
                        and session.browser.is_connected()
                        or not session.proxy.closed
                        or session.proxy.server
                        and session.proxy.server.is_serving()
                        or any(not task.done() for task in session.proxy.tasks)
                    ):
                        raise error("browser_forget_not_ready", "浏览器资源尚未关闭，不能清除。")
                    remove_session_files(self.root, owner, sid, "browser")
                    temporary = session.temporary_directory
                    if temporary.exists() or temporary.is_symlink():
                        if temporary.parent != Path("/tmp") or not temporary.name.startswith(
                            "workbench-browser-"
                        ):
                            raise error("browser_forget_path_invalid", "会话临时目录归属无法确认。")
                        private_directory(temporary)
                        remove_private_directory(temporary)
                    session.observation = None
                    session.cache.clear()
                    session.refs.clear()
                    session.link_hrefs.clear()
                    session.reader = None
                    self.sessions.pop(sid)
            else:
                # GovernedBrowser validates the still-present durable row first.
                if not durable_closed:
                    raise error("browser_session_not_found", "浏览器会话归属未确认。", 404)
                remove_session_files(self.root, owner, sid, "browser")
            self.creates = {key: value for key, value in self.creates.items() if value[2] != sid}
        return SessionForgotten(session_id=sid)
