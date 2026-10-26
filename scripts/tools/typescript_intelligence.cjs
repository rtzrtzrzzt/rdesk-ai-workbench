// A single request using the installed TypeScript API. No project code or plugins execute.
const fs = require('node:fs');
const path = require('node:path');
const ts = require(process.argv[2]);
const request = JSON.parse(process.argv[3]);
const root = fs.realpathSync(request.root);
const library = fs.realpathSync(path.dirname(process.argv[2]));
const denied = new Set(request.excluded);
const protectedPaths = request.protected.map(p => path.resolve(p));
const cache = new Map();
let readBytes = 0;
let limited = false;
const inside = (base, file) => file === base || file.startsWith(base + path.sep);
function permitted(file, libraries = true) {
  try {
    const real = fs.realpathSync(file);
    if (libraries && inside(library, real) && /^lib\..*\.d\.ts$/.test(path.basename(real))) {
      return real;
    }
    if (!inside(root, real) || protectedPaths.some(base => inside(base, real))) return null;
    if (path.relative(root, real).split(path.sep).some(p => denied.has(p) || p === '.env' || p.startsWith('.env.'))) return null;
    return real;
  } catch { return null; }
}
function readFile(file) {
  const real = permitted(file);
  if (!real || !/\.(?:[cm]?[jt]sx?|json)$/i.test(real)) return undefined;
  if (cache.has(real)) return cache.get(real);
  try {
    const size = fs.statSync(real).size;
    if (size > request.maxFileBytes || readBytes + size > 16 * 1024 * 1024 || cache.size >= 1000) {
      limited = true;
      return undefined;
    }
    const value = fs.readFileSync(real, 'utf8');
    readBytes += Buffer.byteLength(value);
    cache.set(real, value);
    return value;
  } catch { return undefined; }
}
const io = {
  ...ts.sys,
  readFile,
  fileExists: file => Boolean(permitted(file)) && ts.sys.fileExists(file),
  directoryExists: directory => Boolean(permitted(directory)) && ts.sys.directoryExists(directory),
  realpath: file => permitted(file) || file,
  getDirectories: directory => permitted(directory) ? ts.sys.getDirectories(directory).filter(p => permitted(path.join(directory, p))) : [],
  readDirectory: (directory, extensions, excludes, includes, depth) => {
    if (!permitted(directory)) return [];
    // TypeScript's matcher follows only directories allowed by this host, preventing
    // traversal of node_modules, runtime data, secrets and symlinked outside trees.
    return ts.matchFiles(directory, extensions, excludes, includes, true, root, depth,
      entry => {
        const entries = fs.readdirSync(entry, { withFileTypes: true });
        return {
          files: entries.filter(e => e.isFile() && permitted(path.join(entry, e.name))).map(e => e.name),
          directories: entries.filter(e => e.isDirectory() && permitted(path.join(entry, e.name))).map(e => e.name),
        };
      }, file => permitted(file) || file);
  },
};
let options = { allowJs: true, checkJs: true, noEmit: true, target: ts.ScriptTarget.ESNext, jsx: ts.JsxEmit.Preserve };
let names = [request.path];
let config = path.join(path.dirname(request.path), 'tsconfig.json');
while (!io.fileExists(config) && path.dirname(config) !== root) {
  const directory = path.dirname(path.dirname(config));
  if (!inside(root, directory)) break;
  config = path.join(directory, 'tsconfig.json');
}
if (io.fileExists(config)) {
  const loaded = ts.readConfigFile(config, readFile);
  if (!loaded.error) {
    const parsed = ts.parseJsonConfigFileContent(loaded.config, io, path.dirname(config));
    options = { ...parsed.options, noEmit: true };
    names = [...new Set([request.path, ...parsed.fileNames])];
  }
}
const host = {
  ...io,
  useCaseSensitiveFileNames: () => ts.sys.useCaseSensitiveFileNames,
  getCompilationSettings: () => options,
  getScriptFileNames: () => names,
  getScriptVersion: () => '1',
  getScriptSnapshot: file => {
    const content = readFile(file);
    return content === undefined ? undefined : ts.ScriptSnapshot.fromString(content);
  },
  getCurrentDirectory: () => root,
  getDefaultLibFileName: settings => ts.getDefaultLibFilePath(settings),
};
const service = ts.createLanguageService(host);
const program = service.getProgram();
const source = program.getSourceFile(request.path);
if (!source) throw new Error('Target source is not available within the file budget.');
const position = source.getPositionOfLineAndCharacter(request.line - 1, request.column - 1);
const range = (file, start, length) => {
  const begin = file.getLineAndCharacterOfPosition(start);
  const end = file.getLineAndCharacterOfPosition(start + length);
  return { start: { line: begin.line + 1, column: begin.character + 1 }, end: { line: end.line + 1, column: end.character + 1 } };
};
const location = entry => {
  const real = permitted(entry.fileName, false);
  if (!real) return null;
  const file = program.getSourceFile(entry.fileName);
  return file ? { path: path.relative(root, real), range: range(file, entry.textSpan.start, entry.textSpan.length) } : null;
};
let items;
if (request.operation === 'definition') {
  items = (service.getDefinitionAtPosition(request.path, position) || []).map(location).filter(Boolean);
} else if (request.operation === 'references') {
  items = (service.getReferencesAtPosition(request.path, position) || []).map(location).filter(Boolean);
} else if (request.operation === 'symbols') {
  items = [];
  function visit(entries, parents = []) {
    for (const entry of entries) {
      if (entry.kind !== 'script' && entry.spans.length) {
        items.push({ name: entry.text, kind: entry.kind, container: parents.join('.'), path: path.relative(root, request.path), range: range(source, entry.spans[0].start, entry.spans[0].length) });
      }
      visit(entry.childItems || [], [...parents, entry.text]);
    }
  }
  visit(service.getNavigationBarItems(request.path));
} else if (request.operation === 'diagnostics') {
  items = [...service.getSyntacticDiagnostics(request.path), ...service.getSemanticDiagnostics(request.path)].map(diagnostic => ({
    message: ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n'),
    code: diagnostic.code,
    severity: diagnostic.category === ts.DiagnosticCategory.Error ? 1 : 2,
    source: 'typescript',
    path: path.relative(root, request.path),
    range: range(source, diagnostic.start || 0, diagnostic.length || 0),
  }));
} else throw new Error('Unknown operation');
const result = { backend: 'typescript', available: true, items: [], truncated: limited, position_encoding: 'utf-16' };
for (const item of items) {
  if (result.items.length >= request.max_results || Buffer.byteLength(JSON.stringify({ ...result, items: [...result.items, item] })) > 31000) {
    result.truncated = true;
    break;
  }
  result.items.push(item);
}
service.dispose();
process.stdout.write(JSON.stringify(result));
