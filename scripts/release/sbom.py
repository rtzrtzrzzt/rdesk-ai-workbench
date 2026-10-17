"""CycloneDX lock inventory, with explicit bundled/locked evidence boundaries."""

import json
import re
from urllib.parse import quote


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def properties(**values):
    return [
        {
            "name": "workbench:" + key,
            "value": value if isinstance(value, str) else json.dumps(value, sort_keys=True),
        }
        for key, value in values.items()
    ]


def inventory(root, uv, npm, notices, read_source):
    materials, components, edges = {}, [], {}
    licenses = {}
    for line in notices.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 3 and "https://pypi.org/pypi/" in cells[0]:
            match = re.match(r"\[([^]]+)\]", cells[0])
            if match:
                licenses[(normalized(match[1]), cells[1])] = cells[2]
    python = [p for p in uv["package"] if p.get("source", {}).get("editable") != "."]
    refs = {}
    for package in python:
        ref = f"pkg:pypi/{normalized(package['name'])}@{package['version']}"
        refs.setdefault(normalized(package["name"]), []).append((package["version"], ref))
    for package in python:
        name, version = package["name"], package["version"]
        ref = f"pkg:pypi/{normalized(name)}@{version}"
        component = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": ref,
            "properties": properties(
                distribution="locked_not_bundled",
                lockfile="uv.lock",
                platform_installation="not_asserted",
                source=package.get("source", {}),
                resolution_markers=package.get("resolution-markers", []),
                locked_artifacts={
                    key: package[key] for key in ("sdist", "wheels") if key in package
                },
            ),
        }
        if label := licenses.get((normalized(name), version)):
            component["licenses"] = [{"license": {"name": label}}]
            component["properties"] += properties(
                license_evidence="THIRD_PARTY_NOTICES.md exact locked version"
            )
        else:
            component["properties"] += properties(
                license_evidence="unknown; no assertion in existing notices"
            )
        components.append(component)
        dependencies = list(package.get("dependencies", []))
        for group in package.get("optional-dependencies", {}).values():
            dependencies.extend(group)
        edges[ref] = sorted(
            {
                target
                for dep in dependencies
                for dep_version, target in refs.get(normalized(dep["name"]), [])
                if "version" not in dep or dep["version"] == dep_version
            }
        )
    npm_refs = {}
    for location, package in npm["packages"].items():
        if not location:
            continue
        name = location.rsplit("node_modules/", 1)[-1]
        version = package.get("version", "unresolved")
        ref = f"npm:{location}@{version}"
        npm_refs[location] = ref
        license_files = []
        encoded = quote(location, safe="") + "@" + version
        installed = root / "apps/web" / location
        existing = root / "THIRD_PARTY_LICENSES/npm" / encoded
        origin = None
        try:
            metadata = json.loads(read_source(installed / "package.json"))
            if metadata.get("version") == version:
                origin = installed
        except (OSError, ValueError):
            pass
        if origin is None and existing.is_dir():
            origin = existing
        if origin:
            for path in sorted(origin.rglob("*")):
                relative = path.relative_to(origin)
                if len(relative.parts) > 3 or "node_modules" in relative.parts:
                    continue
                if not re.match(r"^(licen[cs]e|copying|notice)([._-].*)?$", path.name, re.I):
                    continue
                if path.is_file():
                    destination = f"THIRD_PARTY_LICENSES/npm/{encoded}/{relative.as_posix()}"
                    materials[destination] = read_source(path)
                    license_files.append(destination)
        if not package.get("dev") and not license_files:
            raise ValueError(f"Missing actual frontend license material for {location}@{version}")
        component = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": f"pkg:npm/{quote(name, safe='/')}@{quote(version, safe='')}",
            "properties": properties(
                distribution="locked; bundle inclusion not proven without build graph",
                lockfile="apps/web/package-lock.json",
                install_path=location,
                dev=bool(package.get("dev")),
                optional=bool(package.get("optional")),
                platform_installation="not_asserted",
                integrity=package.get("integrity", "unknown"),
                license_materials=license_files,
                cpu=package.get("cpu", []),
                os=package.get("os", []),
            ),
        }
        if package.get("license"):
            component["licenses"] = [{"license": {"name": package["license"]}}]
        if package.get("resolved"):
            component["externalReferences"] = [{"type": "distribution", "url": package["resolved"]}]
        components.append(component)
    for location, package in npm["packages"].items():
        if not location:
            continue
        dependencies = set()
        for name in {**package.get("dependencies", {}), **package.get("optionalDependencies", {})}:
            parent = location
            while True:
                candidate = f"{parent}/node_modules/{name}" if parent else f"node_modules/{name}"
                if candidate in npm_refs:
                    dependencies.add(npm_refs[candidate])
                    break
                if not parent:
                    break
                parent = parent.rsplit("/node_modules/", 1)[0] if "/node_modules/" in parent else ""
        edges[npm_refs[location]] = sorted(dependencies)
    root_package = next(p for p in uv["package"] if p.get("source", {}).get("editable") == ".")
    direct = []
    for dep in root_package.get("dependencies", []):
        direct.extend(ref for _, ref in refs.get(normalized(dep["name"]), []))
    edges["ai-workbench"] = sorted(set(direct) | {"ai-workbench-web-static"})
    edges["ai-workbench-web-static"] = sorted(
        npm_refs[f"node_modules/{name}"]
        for name in npm["packages"][""].get("dependencies", {})
        if f"node_modules/{name}" in npm_refs
    )
    return components, edges, materials
