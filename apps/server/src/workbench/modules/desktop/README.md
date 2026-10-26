# 原生 DesktopProvider

本模块是受信本机执行适配器，不是 Agent、业务账本或 OS 沙箱。当前经 automation 模块、持久租约、原生 companion、HTTP 与前端接入；本模块仍只承担原生驱动职责。复用现有 Pillow13.0.0、Python stdlib 与系统原生库。主入口注入归属、明确用户授权、节点配对、持久单写租约与 Operation，页面/模型输出不能自行授权。

## 已实现范围与依赖

2026-10-19产品授权增量：工作台使用默认开启的用户级电脑总开关，host可在明确授权下传入`DesktopGrant.all_windows=true`。此时捕获/坐标范围为显示器并集，可在重新观察后操作不同窗口；前台身份、截图代数、几何、锁屏和持久单写租约继续校验。开关关闭会即时撤权并关闭旧会话。原生库默认grant仍不授权任何操作，实际AI动作继续走独立本地审核；驱动能力不会由开关变成已实现。

Linux真实环境为DGX Spark aarch64、内核7.0.0-1019-nvidia、GNOME X11、DISPLAY=:1，X.Org21.1.11。已检测到libX11/libXtst、XTEST扩展及Pillow XCB；xrandr显示一个USB-C-1、1920×1200显示器，GNOME GetActive实际false。不是仅根据DISPLAY推定可控。

Linux驱动使用受限ctypes X11/XTEST绑定和Pillow XCB；没有下载新的鼠标键盘包、第二套桌面Agent框架或虚拟显示器。只读系统命令是固定参数的xrandr与GNOME DBus锁屏查询，不接收调用者shell文本。没有任意脚本、JS、选择器、剪贴板、文件传输、摄像头或音频接口。

Wayland检测到会话类型或WAYLAND_DISPLAY即明确返回desktop_portal_required，不退到XWayland或未经授权的系统级输入。需要Portal ScreenCast/RemoteDesktop、PipeWire及用户交互授权的组合，当前没有伪造其可用状态，也没有安装重型组件。XTEST是系统输入原语，不能等同应用业务回执。

Windows实现User32前台窗口/PID/几何、交互desktop可访问性、物理像素DPI上下文、Pillow捕获及有限SendInput原语；检查BOOL与实际插入事件数。UIPI、安全桌面、服务会话限制不提权绕过。当前没有Windows设备，输入/捕获都等待真实环境验证。macOS实现CoreGraphics显示器与指针、Pillow捕获及原生TCC屏幕录制/AX权限探测，不自动弹权限提示；由于尚缺实际安全前台/锁屏/输入证据，控制明确unsupported，不仅凭AX=true就CGEventPost。没有macOS设备，捕获/权限与Retina映射等待真实验证。

## 稳定接口

`DesktopService(data_dir, *, node_id='local-desktop', enabled=False, allow_control=False, assert_lease=None)` 不在导入时启动或捕获。异步公共方法：

- `probe() -> DesktopCapability`
- `create(owner:DesktopOwner,command:DesktopCreate,grant:DesktopGrant) -> DesktopSession`
- `observe(owner,sid,command:DesktopObserve|None=None) -> DesktopObservation`
- `action(owner,command:DesktopAction,lease:DesktopLease) -> DesktopActionResult`
- `stop(owner,sid,*,takeover=False) -> DesktopStop`

`DesktopClient(data_dir)` 复用私有原生传输：Linux/macOS 使用 UDS，Windows 使用带高熵 token 的私有回环端口；Linux已实测，其他平台等待设备验证。当前没有远端节点配对服务，详见[传输说明](../automation/transport_README.md)。所有平台可使用 `DesktopClient(service=trusted_service)` 原生本地接口；这不意味着远端电脑已配对。客户端不带工作台cookie、不自动重试，动作传输中断为结果不明。调用取消会请求停止该会话。主后端/Agent工具应共用此入口，而非直接暴露native.perform。

`DesktopOwner`：user_id、workspace_id、run_id(nullable)，可信注入UUID，每次严格匹配。`DesktopGrant`：grant_id、capture=false、control=false、all_windows=false、target_window_id(nullable)、target_window_pid(nullable)、expires_at。`DesktopCreate`：request_id、mode=observe|control(默认observe)、target_window_id、ttl_seconds(10..900，默认300)、manual_takeover_ack=false。同request同payload返回同会话，不创建第二个控制器；异payload409。底层默认没有观察授权，更没有控制升级。

control模式必须主进程allow_control=true、grant.control=true、已知未锁屏、驱动输入实现、manual_takeover_ack=true，以及主入口提供同步 `assert_lease(owner,session_view,lease)->None` 验证器。固定窗口grant还必须匹配精确当前前台window_id+PID；全窗口grant允许重新观察另一个前台窗口，动作仍绑定该次真实窗口/PID。该回调在本机工作线程每个输入原语前执行，主调用方必须核验真实持久租约owner/run/node/epoch/期限及最新授权，失败抛安全AppError。不能用一个恒真回调冒充持久租约。CLI没有打开控制的参数。

`DesktopLease`：lease_id、grant_id、epoch>=1、expires_at，必须由持久租约服务生成。模块仅进行本机期限/grant绑定检查并调用验证器，不另造DB租约、批准或恢复账本。同实例最多一个control会话、4个活动会话、128条会话历史；该本机限制不能阻止其他应用操作桌面，跨进程/节点单写由主持久租约保障。

`DesktopObservation`：node_id/session_id/observation_id、generation、geometry_id、captured_at、displays、window_id/window_pid、bounds、screenshot_path、content_hash/byte_size、image_width/height、logical_width/height、pixel_ratio_x/y、transform=identity_crop_offset、accessibility_tree_available=false、locked(nullable)。固定窗口grant捕获实际前台窗口区域，全窗口grant捕获显示器并集，保留前台身份作为动作栅栏；不是无障碍树。X11在root像素坐标内裁剪，所以实测比例1.0；Windows使用物理像素DPI上下文；macOS保留真实输出比例，不能猜Retina倍率。

geometry_id基于原生显示器、窗口/PID、窗口矩形与会话身份；几何或前台变化会递增generation并拒绝旧动作。坐标是原始截图像素，输入前换算到此次窗口/屏幕坐标，截图显示缩放不改变动作坐标。没有裁剪旋转/猜测映射接口。屏幕内容可以在同一几何内自行更新，当前没有可靠内容/无障碍树变化监听；不能宣称捕获后画面绝不会变化。每次动作后都必须重新observe。

`DesktopAction`：request_id、session_id、last_observation、generation、geometry_id、target_window_id、kind=click|type|key|scroll。click仅左键单击，x/y必须在原截图；scroll含x/y与-10..10步；key限Enter/Tab/Escape/Backspace/Delete/方向/Home/End/PageUp/PageDown/Space，不开放任意组合或系统快捷键；type最多256字符，X11只支持当前键盘映射level0/1的可打印ASCII，不能无损输入中文或任意Unicode，不改键盘映射/剪贴板。WindowsUnicode输入实现未实测；macOS控制拒绝。没有拖拽/长按/后台窗口输入的假能力。

每次动作只消费当前观察；前台窗口/PID、锁屏、屏幕几何、当前指针/持有键或授权租约变化立即拒绝；不会激活目标窗口、偷焦点或自动转控另一个窗口。指针/持有键只能发现部分人工活动，无法可靠发现一次快速按键、所有触摸或其他应用的输入。因此capability明确human_input_detection=unsupported、explicit_takeover_required=true，控制前必须约定人工显式接管；不能以轮询宣称完整人工输入检测。

## 超时、停止与结果

动作每原语重查停止/期限/租约/目标，3秒预算，不发送长队列。服务响应预算3.2秒；若本机API仍未返回，取消标志生效、会话stopping禁止后续动作，并报告pending，不假称Python线程已被强杀。线程完成前不会重新开启同一控制目标。原生OS调用本身不能承诺硬实时中止，已排入OS输入的效果可能晚到，必须保留结果不明；不会杀用户进程或整个桌面。

X11/Windows只跟踪并释放此驱动积极发送的按键/按钮，不执行全键释放、不重置用户修饰键。配对按下/释放后检查原生返回；失败时stop尝试仅释放这些owned输入，无法确认则owned_inputs_released=false/stopping，需本机人工核对。没有持久按键/按钮跨动作接口。

`DesktopActionResult.execution_status` 为executed/not_executed/outcome_unknown，business_outcome永远unverified，附起止时间/error_code/message，requires_observe=true。executed仅表示原生函数返回，不代表网站/应用保存或提交。原生调用后的超时/断线/失败保留unknown，不自动重放。每会话最多128动作结果用于同request去重，持久副作用恢复仍由主Operation账本处理。

`DesktopStop`：session_id、status=stopped|stopping|taken_over、generation、pending_native_call、owned_inputs_released、external_effects_reverted=false。stop/takeover先设置线程安全取消标志并使旧观察失效，不排队等待动作锁；pending时明确回报停止待确认，稍后再次stop核对。takeover不会自动重新授权或恢复旧动作。

本机不依赖模型/API的紧急入口：

```sh
.venv/bin/python -m workbench.modules.desktop.entrypoint --stop --data-dir .local/acceptance/data
```

它写私有desktop/STOP标记；工作线程每个原语前直接检查，拒绝新会话/动作。标记不会自动清除。用户核对现状、主入口撤销旧租约且显式重新授权后，由可信主程序清除；模型没有清除接口。直接SIGTERM只能停止本companion，不代表已撤回OS效果。

## 只读 companion 与截图存储

```sh
.venv/bin/python -m workbench.modules.desktop.entrypoint \
  --enable-observation --data-dir .local/acceptance/data
```

该 standalone 观察入口只监听0700目录内0600 desktop/companion.sock，默认仍需每次真实capture grant。已有socket不覆写，正常SIGTERM移除本次inode端点；不监听公共TCP、未实现远端配对。产品使用主 Launcher 已接入的 automation companion，由组合层传入真实租约验证；不能用这个 CLI 观察入口代替持久授权或隐式开启全桌面控制。ctypes Xlib错误处理器限定这个专用companion，应避免嵌入其他依赖Xlib全局错误处理的GUI进程。

默认截图保存 `data_dir/desktop/observations/<user_id>/<session_id>/desktop-<uuid>.png`，目录私有、文件0600，不打印内容或向外发送；或可信调用方指定当前workspace现有run/attempt staging绝对路径。PNG为新文件不覆盖原件，返回hash和来源，主文件服务再走现有artifact发布协议，模块不写DB。前端不能直接把本机路径当下载URL。**当前项目电脑截图只允许本地模型，禁止传给远程模型，普通附件传输同意不能解除该限制。** 每会话16张、每companion128MiB，单图16MiB/3200万像素。生命周期保留与删除由主应用明确治理，不靠停止静默抹掉截图证据。

## 实际辅助证据（2026-10-17）

只读X11原生快照/前台PID/几何通过。按用户已授权本项目验收捕获实际窗口PNG1854×1168、189053字节、0600、原始到逻辑像素比例1.0；PNG结构校验通过，观察ID为acbbd189-7f5c-4173-81b8-c91098c2fc2a，保留在私有数据。没有读取/打印屏幕内容，没有发给模型或外部服务。probe从waiting_verification变为capture available；完整platform_verified仍false，因为输入未真实验收。

真实observe grant无法创建control（desktop_control_not_authorized），显式takeover使generation=2、旧观察失效。只读companion/client真实UDS0600通信通过，SIGTERM移除本次socket。本机CLI实际STOP标记阻断新会话（desktop_emergency_stop）；辅助检查完成、所有自有会话已停止后只删除本轮标记，未留下阻塞用户的停止状态。

上述2026-10-17辅助检查没有Xvfb/Xephyr，也未创建窗口或调用输入原语。2026-10-18 主 agent 已通过产品 UI 在新建自有 Chrome 的英文输入环境中捕获、提交 XTEST ASCII 输入，真实 textarea 收到 `Work: 1`；人工接管回执为 taken_over、owned_inputs_released=true、pending_native_call=false，随后撤销授权并停止运行时。证据为本机 `.local/installation-acceptance/desktop-control-ui-evidence.json`。这不等于全部桌面动作、中文或其他平台通过。

本机原 Chrome/libpinyin 环境会吞掉字符 keydown，DOM 无 input/composition；20ms/100ms 停留均无效。仅对新建目标 Chrome 设置进程级 `GTK_IM_MODULE=xim`、`XMODIFIERS=@im=none` 的对照成功，全局 IBus 保持 libpinyin。产品没有静默改输入法、键盘映射、剪贴板或自动补发；界面明确提醒重新观察目标窗口确认文字。OS executed 是输入原语回执，不能证明应用内容已改变。
