/**
 * 聊天相关类型定义
 */

export type MessageRole = "user" | "assistant" | "system";

// 多模态消息内容项
export type MessageContentPart =
  | { type: "text"; text: string }
  | { type: "image"; url: string };

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  contentParts?: MessageContentPart[]; // 多模态内容
  imageUrl?: string; // 兼容旧版单图
  imageUrls?: string[]; // 新版多图
  timestamp: number;
  loading?: boolean;
  streaming?: boolean; // 是否正在流式输出
}

// 会话元数据
export interface Session {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

// 收藏食谱
export interface Favorite {
  id: number;
  title: string;
  content: string;
  ingredients: string;
  created_at: string;
  updated_at: string;
}

export interface Recipe {
  title: string;
  score?: number;
  reason?: string;
  difficulty?: string;
  url?: string;
  steps?: string[];
  seasonings?: string[];
  cooking_time?: string;
}
