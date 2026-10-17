# Fixed dependency / protocol evidence

Checked2026-10-17 against official PyPI metadata, the locally installed source and
official upstream pages. This is limited to `mcp==2.3.0` and
`jsonschema==4.26.0`, not a repository-wide security audit.

| Item | Actual evidence |
| --- | --- |
| mcp2.3.0 | Official stable v2 release; Python>=3.10; MIT; PyPI wheel368664 bytes, uploaded2026-10-02T22:06:52Z. No CLI/rich extras needed. |
| mcp-types2.3.0 | Exact SDK requirement, MIT; Python>=3.10. |
| jsonschema4.26.0 | MIT; Python>=3.10; default PyPI current release at the check. Existing pydantic2.14.1/anyio4.15.1 satisfy SDK constraints. |
| HTTP | Installed mcp metadata requires httpx2>=2.10.0. Actual root env has httpx2/httpcore2 2.13.1 separately from httpx0.28.1. No replacement of the app HTTP client. |
| Maintenance | Official SDK v2 stable supports modern2026-07-28 and earlier revisions; v1 maintenance branch continues critical/security fixes. jsonschema supports its latest release. |

Sources: [PyPI MCP2.3.0 metadata](https://pypi.org/pypi/mcp/2.3.0/json),
[official release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0),
[official repository / maintenance](https://github.com/modelcontextprotocol/python-sdk),
[jsonschema4.26.0 metadata](https://pypi.org/pypi/jsonschema/4.26.0/json),
[jsonschema security policy](https://github.com/python-jsonschema/jsonschema/security).

## Targeted public vulnerability query

Two actual POST queries to `https://api.osv.dev/v1/query`, with
`package.ecosystem=PyPI`, exact names/versions `mcp/2.3.0` and
`jsonschema/4.26.0`, each returned `{}` on2026-10-17. That means no matching
published OSV advisory was returned at this time, not that the packages are
free of undisclosed or application-specific vulnerabilities.

The official GitHub repository advisory APIs returned12 published MCP advisories
and an empty jsonschema advisory list. The reported affected ranges did not
include mcp2.3.0. Particularly relevant recent client issues were already fixed
in2.2.0:

- [GHSA-rwrf-2pqf-9j8j](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-rwrf-2pqf-9j8j): untrusted output-schema `$ref` retrieval; affected2.0.0–<2.2.0.
- [GHSA-5h93-6whr-6q8j](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-5h93-6whr-6q8j): cross-origin redirect custom-header/body leakage; affected2.0.0–<2.2.0.
- [GHSA-qx49-fqc8-xw99](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-qx49-fqc8-xw99): OAuth credential disclosure to a server-selected authorization server; affected2.0.0a1–<2.2.0.
- [GHSA-fmmv-w9g8-j3gc](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-fmmv-w9g8-j3gc): unbounded server request bodies; fixed2.1.0 for v2. The module is a client; its acceptance HTTP server also explicitly sets a body cap.

The other returned ranges cover older v1 or v2<2.2.0. Query sources:
[MCP published advisory API](https://api.github.com/repos/modelcontextprotocol/python-sdk/security-advisories),
[jsonschema published advisory API](https://api.github.com/repos/python-jsonschema/jsonschema/security-advisories),
[OSV exact-version query API](https://google.github.io/osv.dev/post-v1-query/).
The separate `check-jsonschema` project/package is not this dependency and is not installed for this module.

Module policy still refuses external schema refs, automatic OAuth/URL elicitation,
sampling and roots; a patch-version check is not treated as permission.

## Version-specific public APIs verified

- [Protocol modes](https://py.sdk.modelcontextprotocol.io/protocol-versions/): auto discovery/fallback, legacy initialize, pinned modern without negotiation, public prior_discover.
- [Client transports](https://py.sdk.modelcontextprotocol.io/client/transports/): SDK stdio clean environment/private owned process cleanup; custom httpx2 client and max_sse_event_size for StreamableHTTP.
- [Multi-round-trip requests](https://py.sdk.modelcontextprotocol.io/handlers/multi-round-trip/): modern input_required/request_state/input_responses, single-exchange low-level session path and remote state-key lifetime limits.
- [Client callback declaration](https://py.sdk.modelcontextprotocol.io/client/callbacks/): registering elicitation_callback advertises both form/url; no public form-only option. Advertised SDK capability and effective host policy must be shown separately.

Actual installed signatures/source were inspected before implementation:
`mcp/client/client.py`, `mcp/client/session.py`, `mcp/client/stdio.py`,
`mcp/client/streamable_http.py`, `mcp_types/_types.py`. The module does not modify
or override private SDK methods, does not run Client.call_tool's automatic
continuation loop, and does not register any legacy callbacks.
