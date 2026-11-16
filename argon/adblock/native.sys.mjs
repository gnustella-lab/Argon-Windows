// SPDX-License-Identifier: MPL-2.0
const { ctypes } = ChromeUtils.importESModule('resource://gre/modules/ctypes.sys.mjs');
const PLATFORM_LIBRARIES = {
  Linux: {
    filename: 'libargon_adblock.so',
    extension: '.so',
    chromeUrl: 'chrome://browser/content/argon/adblock/libargon_adblock.so',
    deleteWhileOpen: true,
  },
  WINNT: {
    filename: 'argon_adblock.dll',
    extension: '.dll',
    chromeUrl: 'chrome://browser/content/argon/adblock/argon_adblock.dll',
    deleteWhileOpen: false,
  },
};

function getPlatformLibrarySpec(os = Services.appinfo?.OS) {
  return PLATFORM_LIBRARIES[os] || null;
}

function resolveChromeFilePath(chromeUrl) {
  try {
    const uri = Services.io.newURI(chromeUrl);
    const chromeRegistry = Cc['@mozilla.org/chrome/chrome-registry;1']?.getService?.(Ci.nsIChromeRegistry);
    const resolved = chromeRegistry?.convertChromeURL?.(uri);
    if (resolved?.scheme === 'file') {
      const file = resolved.QueryInterface(Ci.nsIFileURL)?.file;
      if (file?.exists?.() && file.isFile?.()) return file.path;
    }
  } catch {}
  return null;
}

function resolveBinDirLibraryPath(filename) {
  for (const dirKey of ['GreBinD', 'CurProcD', 'XCurProcD']) {
    try {
      const dir = Services.dirsvc?.get?.(dirKey, Ci.nsIFile);
      if (!dir) continue;
      const candidate = dir.clone();
      candidate.append(filename);
      if (candidate.exists?.() && candidate.isFile?.()) return candidate.path;
    } catch {}
  }
  return null;
}

function resolveUnpackedLibraryPath(spec) {
  return resolveChromeFilePath(spec.chromeUrl) || resolveBinDirLibraryPath(spec.filename);
}

function openEngineLibrary(path, tempPath = null) {
  const library = ctypes.open(path);
  try {
    const call = library.declare('execute_json', ctypes.default_abi, ctypes.char.ptr, ctypes.char.ptr);
    return new NativeAdblockEngine(library, call, tempPath);
  } catch (error) {
    library.close();
    throw error;
  }
}

// No privileged WASM/eval exemptions: load the optional bundled native Rust library when present.
export class NativeAdblockEngine {
  static isSupportedPlatform(os = Services.appinfo?.OS) {
    return Boolean(getPlatformLibrarySpec(os));
  }

  static async create() {
    const os = Services.appinfo?.OS;
    const spec = getPlatformLibrarySpec(os);
    if (!spec) {
      throw new Error(`Native adblock library not built for ${os || 'unknown OS'}; keep uBlock fallback`);
    }
    const directPath = resolveUnpackedLibraryPath(spec);
    if (directPath) {
      try {
        return openEngineLibrary(directPath);
      } catch {}
    }
    let response;
    try {
      response = await fetch(spec.chromeUrl);
    } catch (error) {
      throw new Error(`Native adblock library (${spec.filename}) is not bundled; keep uBlock fallback (${error?.message || error})`);
    }
    if (response && response.ok === false) {
      throw new Error(`Native adblock library (${spec.filename}) is not bundled (HTTP ${response.status}); keep uBlock fallback`);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    const path = PathUtils.join(PathUtils.profileDir, 'argon-adblock-' + Services.uuid.generateUUID().toString() + spec.extension);
    await IOUtils.write(path, bytes, { mode: 'create' });
    if (spec.deleteWhileOpen) {
      try {
        return openEngineLibrary(path);
      } finally {
        try { await IOUtils.remove(path, { ignoreAbsent: true }); } catch {}
      }
    }
    // Windows holds an exclusive lock on loaded DLLs until library.close().
    try {
      return openEngineLibrary(path, path);
    } catch (error) {
      try { await IOUtils.remove(path, { ignoreAbsent: true }); } catch {}
      throw error;
    }
  }
  constructor(library, invoke, tempPath = null) {
    this.library = library;
    this.invoke = invoke;
    this.tempPath = tempPath;
  }
  call(value) {
    const result = JSON.parse(this.invoke(JSON.stringify(value)).readString());
    if (result.error) throw new Error(result.error);
    return result;
  }
  close() {
    if (this.library) {
      this.library.close();
      this.library = null;
    }
    if (this.tempPath) {
      const path = this.tempPath;
      this.tempPath = null;
      try { IOUtils?.remove?.(path, { ignoreAbsent: true })?.catch?.(() => {}); } catch {}
    }
  }
  load(lists) { return this.call({ op: 'load', lists }); }
  check(url, source, type, method = 'GET') { return this.call({ op: 'check', url, source, type, method }); }
  cosmetic(url) { return this.call({ op: 'cosmetic', url }); }
}
