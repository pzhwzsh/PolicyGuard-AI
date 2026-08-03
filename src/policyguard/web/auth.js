const loginTab = document.querySelector("#login-tab");
const registerTab = document.querySelector("#register-tab");
const loginForm = document.querySelector("#login-form");
const registerForm = document.querySelector("#register-form");

const errorLabels = {
  credentials_invalid: "邮箱或密码错误。",
  account_temporarily_locked: "失败次数过多，账号已临时锁定15分钟。",
  email_already_registered: "该邮箱已经注册，请直接登录。",
  password_length_invalid: "密码长度必须为10到128位。",
  password_complexity_insufficient: "密码必须同时包含大写字母、小写字母和数字。",
  password_whitespace_forbidden: "密码不能包含空格。",
  password_contains_email: "密码不能包含邮箱用户名。",
  smtp_not_configured: "邮件服务尚未配置，请联系管理员。"
};

function showMode(mode) {
  const login = mode === "login";
  loginForm.hidden = !login;
  registerForm.hidden = login;
  loginTab.classList.toggle("active", login);
  registerTab.classList.toggle("active", !login);
  loginTab.setAttribute("aria-selected", String(login));
  registerTab.setAttribute("aria-selected", String(!login));
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {"Content-Type": "application/json"},
    ...options
  });
  const body = response.status === 204 ? {} : await response.json();
  if (!response.ok) throw new Error(errorLabels[body.detail] || body.detail || "请求失败，请稍后重试。");
  return body;
}

loginTab.addEventListener("click", () => showMode("login"));
registerTab.addEventListener("click", () => showMode("register"));

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = loginForm.querySelector("button[type=submit]");
  const error = document.querySelector("#login-error");
  button.disabled = true;
  error.textContent = "";
  try {
    const data = new FormData(loginForm);
    await request("/api/v1/auth/login", {method: "POST", body: JSON.stringify(Object.fromEntries(data))});
    location.assign("/");
  } catch (cause) { error.textContent = cause.message; }
  finally { button.disabled = false; }
});

registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = registerForm.querySelector("button[type=submit]");
  const error = document.querySelector("#register-error");
  button.disabled = true;
  error.textContent = "";
  try {
    const data = Object.fromEntries(new FormData(registerForm));
    await request("/api/v1/auth/register", {method: "POST", body: JSON.stringify(data)});
    location.assign("/");
  } catch (cause) { error.textContent = cause.message; }
  finally { button.disabled = false; }
});

request("/api/v1/auth/me").then(() => location.assign("/")).catch(() => {});
