(function () {
  "use strict";

  if (window.__yanaiLazycatAutologin) return;
  window.__yanaiLazycatAutologin = true;

  var busy = false;
  var AUTH_KEY = "chatgpt2api_auth_key";
  var AUTH_SESSION = "chatgpt2api_auth_session";
  var DONE_FLAG = "yanai_lazycat_autologin_done";

  function routeForRole(role) {
    return "/image/";
  }

  function openAuthDb(mode) {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open("chatgpt2api");
      req.onerror = function () {
        reject(req.error || new Error("indexedDB open failed"));
      };
      req.onupgradeneeded = function (ev) {
        var db = ev.target.result;
        if (!db.objectStoreNames.contains("auth")) {
          db.createObjectStore("auth");
        }
      };
      req.onsuccess = function (ev) {
        var db = ev.target.result;
        if (!db.objectStoreNames.contains("auth")) {
          reject(new Error("auth store missing"));
          return;
        }
        resolve(db.transaction("auth", mode));
      };
    });
  }

  function readAuthKey() {
    return openAuthDb("readonly").then(function (tx) {
      return new Promise(function (resolve, reject) {
        var req = tx.objectStore("auth").get(AUTH_KEY);
        req.onsuccess = function () {
          resolve(String(req.result || "").trim());
        };
        req.onerror = function () {
          reject(req.error);
        };
      });
    });
  }

  function saveSession(data) {
    var session = {
      key: data.token,
      role: data.role,
      subjectId: data.subject_id,
      name: data.name,
      email: data.email,
      quota: data.quota,
    };
    return openAuthDb("readwrite").then(function (tx) {
      tx.objectStore("auth").put(session.key, AUTH_KEY);
      tx.objectStore("auth").put(session, AUTH_SESSION);
      return new Promise(function (resolve, reject) {
        tx.oncomplete = function () {
          resolve();
        };
        tx.onerror = function () {
          reject(tx.error);
        };
      });
    });
  }

  function tryAutologin() {
    if (busy || sessionStorage.getItem(DONE_FLAG) === "1") return;
    busy = true;

    readAuthKey()
      .then(function (existingKey) {
        if (existingKey) {
          sessionStorage.setItem(DONE_FLAG, "1");
          return null;
        }
        return fetch("/auth/lazycat/login", {
          method: "POST",
          credentials: "include",
          headers: { Accept: "application/json" },
        }).then(function (res) {
          if (!res.ok) throw new Error("lazycat login failed");
          return res.json();
        });
      })
      .then(function (data) {
        if (!data) {
          busy = false;
          return;
        }
        if (!data.token) throw new Error("missing token");
        return saveSession(data).then(function () {
          sessionStorage.setItem(DONE_FLAG, "1");
          var target = routeForRole(data.role);
          if (location.pathname.indexOf("/login") === 0 || location.pathname === "/") {
            location.replace(target);
          } else {
            location.reload();
          }
        });
      })
      .catch(function () {
        busy = false;
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tryAutologin);
  } else {
    tryAutologin();
  }
})();
