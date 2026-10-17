# 原生驱动依据与许可证

研究与本机核对日期2026-10-17；未新增依赖。Pillow13.0.0已有XCB，系统X.Org21.1.11、libX11.so.6/libXtst.so.6现存。Pillow许可证与系统库分别适用；具体系统许可证保存在 `/usr/share/doc/libx11-6/copyright` 和 `/usr/share/doc/libxtst6/copyright`（X Consortium/Open Group等宽松许可与各源文件声明），来源为X.Org官方发布库，不把一条MIT标签覆盖全部历史声明。

- [Pillow ImageGrab](https://pillow.readthedocs.io/en/stable/reference/ImageGrab.html)：X11 xdisplay显式选择、XCB能力、Windows all_screens、macOS图像倍率。Linux显式xdisplay使本模块不悄悄调用其他屏幕工具作为Wayland降级。
- [Pillow LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE)：现有Pillow依赖许可证，未重新打包或新增二进制。
- [XTEST标准库文档](https://xorg.freedesktop.org/archive/X11R7.5/doc/Xext/xtestlib.html)：QueryExtension与FakeKey/Button/Motion语义；只提供词法输入原语，不提供高层业务同步或结果保证。
- [Xlib官方文档](https://www.x.org/releases/current/doc/libX11/libX11/libX11.html)：XGetGeometry、XTranslateCoordinates、XQueryPointer、XGetWindowProperty、XQueryKeymap、XSync与错误处理；ctypes声明按实际ABI与官方头文件语义。
- [XDG RemoteDesktop Portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)与[ScreenCast](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)：Wayland授权会话、流几何与逻辑坐标；本轮没有实现或冒充Portal授权。
- [Microsoft SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)：必须核对已插入事件数，UIPI可能阻止调用，不能通过提权重试代替授权。
- [OpenInputDesktop](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-openinputdesktop)、[GetCursorPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getcursorpos)、[SetThreadDpiAwarenessContext](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext)：交互桌面访问和DPI上下文，不自动切换到安全桌面。
- [Apple CGPreflightScreenCaptureAccess](https://developer.apple.com/documentation/coregraphics/cgpreflightscreencaptureaccess())与[AXIsProcessTrusted](https://developer.apple.com/documentation/applicationservices/1460720-axisprocesstrusted)：屏幕与辅助功能是分开的原生权限，不由网页授予。
- [Apple CGGetActiveDisplayList](https://developer.apple.com/documentation/coregraphics/cggetactivedisplaylist(_:_:_:))及[CGDisplayBounds](https://developer.apple.com/documentation/coregraphics/cgdisplaybounds(_:))：实际活动显示器与Quartz空间；当前缺目标设备，未把实现接口声称为实机验证。

Linux ctypes绑定没有引入pyautogui/pynput、xdotool或远程Agent运行时；当前依赖足以完成已授权只读捕获和有界原语实现，故不为未验证平台自动安装大框架。若后续采用成熟Portal/原生配对组件，须单独核对当前平台、权限、维护与许可证并由主agent确认依赖。
