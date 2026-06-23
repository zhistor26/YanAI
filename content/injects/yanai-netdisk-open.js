(function () {
  "use strict";

  var DISK = "/_lzc/files/home";

  function normalizePath(raw) {
    var path = raw;
    try {
      path = decodeURIComponent(raw);
    } catch (_) {}
    if (path.indexOf("http") === 0) {
      var url;
      try {
        url = new URL(path);
      } catch (_) {
        throw new Error("无效的文件路径");
      }
      path = url.pathname.replace(/^\/_lzc\/files\/home(?=\/|$)/, "") || url.pathname;
    }
    path = String(path || "").trim().replace(/\.$/, "");
    if (path && path.charAt(0) !== "/") path = "/" + path;
    return path;
  }

  function fetchDiskFile(path) {
    return fetch(DISK + path, { credentials: "include" }).then(function (res) {
      if (!res.ok) throw new Error("fetch disk file failed: " + res.status);
      return res.blob();
    });
  }

  function notifyFileError(message) {
    console.error("[yanai-netdisk-open]", message);
    window.dispatchEvent(
      new CustomEvent("yanai:lazycat-file-error", { detail: { message: String(message || "读取网盘图片失败") } }),
    );
  }

  function deliverReferenceFile(file) {
    window.__yanaiPendingReferenceFile = file;
    if (location.pathname.indexOf("/image") !== 0) {
      location.replace("/image/?from=lazycat");
      return;
    }
    window.dispatchEvent(new CustomEvent("yanai:lazycat-file", { detail: { file: file } }));
  }

  function run() {
    var params = new URLSearchParams(location.search);
    var fileParam = params.get("file");
    if (!fileParam) return;

    var path;
    try {
      path = normalizePath(fileParam);
    } catch (err) {
      notifyFileError(err && err.message ? err.message : err);
      return;
    }
    if (!path) {
      notifyFileError("无效的文件路径");
      return;
    }

    fetchDiskFile(path)
      .then(function (blob) {
        var name = path.split("/").pop() || "reference.png";
        var file = new File([blob], name, { type: blob.type || "application/octet-stream" });
        deliverReferenceFile(file);
      })
      .catch(function (err) {
        notifyFileError(err && err.message ? err.message : err);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
