/**
 * API 调用封装
 */
import { Session, Favorite } from "@/types/chat";
import { getAuthHeaders } from "@/lib/auth";
import type { User } from "@/lib/auth";

const API_BASE = "http://localhost:8001";

/**
 * 合并认证头
 */
function withAuth(extra: Record<string, string> = {}): Record<string, string> {
    return { ...getAuthHeaders(), ...extra };
}

// ========== 认证相关 ==========

/**
 * 用户注册
 */
export async function register(username: string, password: string): Promise<{ token: string; user: User }> {
    const response = await fetch(`${API_BASE}/api/v1/auth/register`, {
        method: "POST",
        body: JSON.stringify({ username, password }),
        headers: { "Content-Type": "application/json" },
    });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "注册失败");
    }
    const data = await response.json();
    return { token: data.access_token, user: data.user };
}

/**
 * 用户登录
 */
export async function login(username: string, password: string): Promise<{ token: string; user: User }> {
    const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: "POST",
        body: JSON.stringify({ username, password }),
        headers: { "Content-Type": "application/json" },
    });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "登录失败");
    }
    const data = await response.json();
    return { token: data.access_token, user: data.user };
}

// ========== OSS 相关 ==========

/**
 * 获取 OSS 预签名上传 URL
 */
export async function getOssPresignUrl(filename: string): Promise<{ uploadUrl: string; accessUrl: string; contentType: string }> {
    const response = await fetch(`${API_BASE}/api/v1/oss/presign?filename=${filename}`, {
        headers: withAuth(),
    });
    if (!response.ok) {
        throw new Error("获取上传 URL 失败");
    }
    const data = await response.json();
    return {
        uploadUrl: data.uploadUrl.trim().replace(/^["']|["']$/g, ''),
        accessUrl: data.accessUrl.trim().replace(/^["']|["']$/g, ''),
        contentType: data.contentType
    };
}

/**
 * 上传图片到 OSS（OSS 直传，不需要认证头）
 * 如果 OSS 未配置 / 上传失败，自动降级为 base64 数据 URL（通义千问等多模态模型支持内嵌 base64）
 */
export async function uploadImageToOss(file: File): Promise<string> {
    // 生成文件名
    const ext = file.name.split(".").pop() || "jpg";
    const filename = `${Date.now()}.${ext}`;

    try {
        // 获取预签名 URL
        const { uploadUrl, accessUrl, contentType } = await getOssPresignUrl(filename);
        if (!uploadUrl || !accessUrl) {
            throw new Error("OSS 预签名 URL 获取为空");
        }

        // 上传图片（PUT 到 OSS，不用带 Authorization）
        const response = await fetch(uploadUrl, {
            method: "PUT",
            body: file,
            headers: {
                "Content-Type": contentType,
            },
        });

        if (!response.ok) {
            throw new Error(`图片上传失败: ${response.status}`);
        }

        return accessUrl;
    } catch (err) {
        // OSS 失败 fallback：转成 base64 数据 URL，直接内嵌到消息里（不依赖公网存储）
        console.warn("OSS 上传失败，使用 base64 内嵌图片模式：", err);
        return await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result as string);
            reader.onerror = () => reject(new Error("图片读取失败"));
            reader.readAsDataURL(file);
        });
    }
}

// ========== 聊天相关 ==========

/**
 * 流式聊天（支持多图）
 */
export async function streamChat(
    message: string,
    onChunk: (chunk: string) => void,
    image_urls?: string[],
    onError?: (error: Error) => void,
    onComplete?: () => void,
    threadId?: string
): Promise<void> {
    try {
        const url = new URL(`${API_BASE}/api/v1/chat/stream`);

        const response = await fetch(url.toString(), {
            method: "POST",
            body: JSON.stringify({
                message,
                image_urls: image_urls || [],
                thread_id: threadId,
            }),
            headers: withAuth({ "Content-Type": "application/json" }),
        });

        if (!response.ok) {
            throw new Error(response.status === 401 ? "登录已过期，请重新登录" : "请求失败");
        }

        const reader = response.body?.getReader();
        if (!reader) {
            throw new Error("无法读取响应流");
        }

        const decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                onComplete?.();
                break;
            }

            const chunk = decoder.decode(value, { stream: true });
            onChunk(chunk);
        }
    } catch (error) {
        onError?.(error as Error);
    }
}

/**
 * 获取聊天历史
 */
export async function getChatHistory(threadId: string): Promise<{ role: string; content: string }[]> {
    const response = await fetch(`${API_BASE}/api/v1/chat/messages?thread_id=${threadId}`, {
        headers: withAuth(),
    });
    if (!response.ok) {
        throw new Error("获取历史消息失败");
    }
    const data = await response.json();
    return data.messages;
}

/**
 * 清空聊天历史
 */
export async function clearChatHistory(threadId: string): Promise<void> {
    const response = await fetch(`${API_BASE}/api/v1/chat/messages?thread_id=${threadId}`, {
        method: "DELETE",
        headers: withAuth(),
    });
    if (!response.ok) {
        throw new Error("清空历史消息失败");
    }
}

/**
 * 列出所有会话
 */
export async function listSessions(): Promise<Session[]> {
    const response = await fetch(`${API_BASE}/api/v1/chat/sessions`, {
        headers: withAuth(),
    });
    if (!response.ok) {
        throw new Error("获取会话列表失败");
    }
    const data = await response.json();
    return data.sessions;
}

/**
 * 删除指定会话
 */
export async function deleteSession(threadId: string): Promise<void> {
    const response = await fetch(`${API_BASE}/api/v1/chat/sessions/${encodeURIComponent(threadId)}`, {
        method: "DELETE",
        headers: withAuth(),
    });
    if (!response.ok) {
        throw new Error("删除会话失败");
    }
}

// ========== 收藏食谱相关 ==========

/**
 * 列出当前用户所有收藏
 */
export async function listFavorites(): Promise<Favorite[]> {
    const response = await fetch(`${API_BASE}/api/v1/favorites`, {
        headers: withAuth(),
    });
    if (!response.ok) {
        throw new Error("获取收藏列表失败");
    }
    const data = await response.json();
    return data.favorites;
}

/**
 * 获取单条收藏详情
 */
export async function getFavorite(favoriteId: number): Promise<Favorite> {
    const response = await fetch(`${API_BASE}/api/v1/favorites/${favoriteId}`, {
        headers: withAuth(),
    });
    if (!response.ok) {
        throw new Error("获取收藏详情失败");
    }
    return await response.json();
}

/**
 * 删除收藏
 */
export async function deleteFavorite(favoriteId: number): Promise<void> {
    const response = await fetch(`${API_BASE}/api/v1/favorites/${favoriteId}`, {
        method: "DELETE",
        headers: withAuth(),
    });
    if (!response.ok) {
        throw new Error("删除收藏失败");
    }
}

/**
 * 手动创建收藏（前端直接调用）
 */
export async function createFavorite(title: string, content: string, ingredients: string = ""): Promise<{ id: number }> {
    const response = await fetch(`${API_BASE}/api/v1/favorites`, {
        method: "POST",
        headers: withAuth({ "Content-Type": "application/json" }),
        body: JSON.stringify({ title, content, ingredients }),
    });
    if (!response.ok) {
        throw new Error("收藏失败");
    }
    return await response.json();
}
