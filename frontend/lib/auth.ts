/**
 * 用户认证工具：token 与用户信息管理
 */

export interface User {
    id: number;
    username: string;
    created_at: string;
}

const TOKEN_KEY = "access_token";
const USER_KEY = "user_info";

/**
 * 获取 token
 */
export function getToken(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem(TOKEN_KEY);
}

/**
 * 保存 token
 */
export function setToken(token: string): void {
    localStorage.setItem(TOKEN_KEY, token);
}

/**
 * 获取当前用户信息
 */
export function getUser(): User | null {
    if (typeof window === "undefined") return null;
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
        return JSON.parse(raw) as User;
    } catch {
        return null;
    }
}

/**
 * 保存用户信息
 */
export function setUser(user: User): void {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
}

/**
 * 清除登录状态
 */
export function clearAuth(): void {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem("thread_id");
}

/**
 * 是否已登录
 */
export function isLoggedIn(): boolean {
    return !!getToken();
}

/**
 * 获取认证请求头
 */
export function getAuthHeaders(): Record<string, string> {
    const token = getToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
}
