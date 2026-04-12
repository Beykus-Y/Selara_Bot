export type AdminLoginResponse =
  | { ok: true; message: string; redirect?: string }
  | { ok: false; message: string; redirect?: string }
