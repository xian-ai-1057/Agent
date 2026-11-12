/**
 * 登入畫面（多人帳號模式）。任何 API 回 401 時 App 只顯示這個畫面，
 * 網址不變；登入成功後回到原本要去的頁面。
 */

import { type FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../../api.ts";
import { loginErrorMessage } from "../../auth.ts";
import { signedIn } from "../../store.ts";
import { Field } from "./Field.tsx";

export function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const userRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const before = document.title;
    document.title = "登入 · Harness";
    userRef.current?.focus();
    return () => {
      document.title = before;
    };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    // 空的欄位先補上，不用送到伺服器。
    if (!username.trim()) {
      userRef.current?.focus();
      return;
    }
    if (!password) {
      passwordRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await api.login(username.trim(), password);
      signedIn();
    } catch (failure) {
      setError(loginErrorMessage(failure));
      setBusy(false);
      setPassword("");
      passwordRef.current?.focus();
    }
  };

  return (
    <main className="login-page">
      <form className="login-card anim-rise" onSubmit={submit} noValidate>
        <div className="login-brand">
          <span className="logo" aria-hidden="true" />
          Harness
        </div>
        <h1 className="login-title">登入</h1>
        <Field
          ref={userRef}
          label="帳號"
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
        <Field
          ref={passwordRef}
          label="密碼"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          error={error}
          onChange={(event) => setPassword(event.target.value)}
        />
        <button
          type="submit"
          className="btn btn-accent login-submit"
          disabled={busy}
        >
          {busy && <span className="spinner spin" aria-hidden="true" />}
          登入
        </button>
        <p className="help login-hint">
          第一次登入請向管理者索取密碼；Docker 部署的管理者帳號是 admin，密碼在
          docker compose logs harness 裡。
        </p>
      </form>
    </main>
  );
}

/**
 * 不必登入的本機模式少了啟動時的鑰匙：伺服器重新啟動過，或是直接打了
 * 不含 ?key= 的網址。請使用者重新打開終端機上的網址。
 */
export function LocalKeyNotice() {
  return (
    <main className="login-page">
      <div className="login-card anim-rise" role="alert">
        <div className="login-brand">
          <span className="logo" aria-hidden="true" />
          Harness
        </div>
        <h1 className="login-title">請重新打開網址</h1>
        <p className="help">
          網頁版每次啟動都會產生新的網址鑰匙。請打開啟動 pyharness web
          時終端機顯示的完整網址（結尾有 ?key=…）。
        </p>
      </div>
    </main>
  );
}
