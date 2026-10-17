# 官方主来源

核对2026-10-17，无新增依赖，只用Python3.12标准库与既有vault权限模块。

- [Python urllib.parse安全注意](https://docs.python.org/3.12/library/urllib.parse.html#url-parsing-security)：拆分函数不完成输入安全验证，本模块另外检查authority/来源字段。
- [RFC6454 Origin](https://www.rfc-editor.org/rfc/rfc6454)：按scheme/host/port比较，不用域名后缀或字符串前缀替代精确同源。
- [Python SSLContext.load_cert_chain](https://docs.python.org/3.12/library/ssl.html#ssl.SSLContext.load_cert_chain)：证书/私钥加载与配对，显式password回调避免交互；加载成功不证明浏览器信任。显式创建SSLContext，不经create_default_context继承SSLKEYLOGFILE。
- [Python ipaddress](https://docs.python.org/3.12/library/ipaddress.html)：loopback/未指定bind与IP规范形式。
- [Uvicorn设置](https://www.uvicorn.org/settings/)及本项目实际CLI：证书参数与proxy_headers属服务器配置，本轮原生直连TLS固定proxy_headers=False。
- [RFC5737示例地址](https://www.rfc-editor.org/rfc/rfc5737)：说明中的192.0.2.20只作配置占位，非实际LAN探测事实。

项目依据：已批准deployment-and-validation.md6.1/6.2/6.4及首次设置章节。身份/权限/最终真实设备验收由主agent接线；Windows密钥ACL直接复用infrastructure/vault/private.py与windows_acl.py，不新建权限体系，不把Linux辅助结论扩展为Windows验收。
