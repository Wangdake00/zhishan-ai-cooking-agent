"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { Message, Session } from "@/types/chat";
import { ChatMessage } from "@/components/ChatMessage";
import { ChatInput } from "@/components/ChatInput";
import { SessionList } from "@/components/SessionList";
import { uploadImageToOss, streamChat, getChatHistory, listSessions, deleteSession } from "@/lib/api";
import { generateUUID } from "@/lib/utils";
import { getUser, clearAuth } from "@/lib/auth";
import type { User } from "@/lib/auth";
import { UtensilsCrossed, ChefHat, Plus, LogOut } from "lucide-react";

export default function Home() {
    const router = useRouter();
    const [messages, setMessages] = useState<Message[]>([]);
    const [processing, setProcessing] = useState(false);
    const [threadId, setThreadId] = useState<string>("");
    const [sessions, setSessions] = useState<Session[]>([]);
    const [user, setUser] = useState<User | null>(null);
    const [authChecked, setAuthChecked] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const messageIdCounter = useRef(0);

    // 加载历史消息
    const loadHistory = async (id: string) => {
        try {
            const history = await getChatHistory(id);
            if (history && history.length > 0) {
                const loadedMessages: Message[] = history.map((msg, index) => {
                    // 处理多模态消息
                    let content = "";
                    let imageUrl: string | undefined;

                    if (typeof msg.content === 'string') {
                        content = msg.content;
                    } else if (Array.isArray(msg.content)) {
                        // 提取文本和图片
                        const parts = msg.content as { type: string; text?: string; url?: string }[];
                        for (const part of parts) {
                            if (part.type === 'text' && part.text) {
                                content += part.text;
                            } else if (part.type === 'image' && part.url) {
                                imageUrl = part.url;
                            }
                        }
                    }

                    return {
                        id: `history_${index}_${Date.now()}`,
                        role: msg.role as "user" | "assistant",
                        content,
                        imageUrl,
                        timestamp: Date.now() - (history.length - index) * 1000,
                    };
                });
                setMessages(loadedMessages);
                messageIdCounter.current = loadedMessages.length;
            } else {
                setMessages([]);
                messageIdCounter.current = 0;
            }
        } catch (error) {
            console.error("加载历史消息失败:", error);
        }
    };

    // 刷新会话列表
    const refreshSessions = async () => {
        try {
            const list = await listSessions();
            setSessions(list);
        } catch (error) {
            console.error("获取会话列表失败:", error);
        }
    };

    // 切换到指定会话
    const handleSelectSession = async (id: string) => {
        if (id === threadId) return;
        setThreadId(id);
        localStorage.setItem("thread_id", id);
        await loadHistory(id);
    };

    // 删除指定会话
    const handleDeleteSession = async (id: string) => {
        const confirmDelete = window.confirm("确定要删除这个会话吗？删除后无法恢复。");
        if (!confirmDelete) return;

        try {
            await deleteSession(id);
            // 从列表中移除
            setSessions((prev) => prev.filter((s) => s.thread_id !== id));
            // 如果删除的是当前会话，新建一个空白会话
            if (id === threadId) {
                const newThreadId = generateUUID();
                localStorage.setItem("thread_id", newThreadId);
                setThreadId(newThreadId);
                setMessages([]);
                messageIdCounter.current = 0;
            }
        } catch (error) {
            console.error("删除会话失败:", error);
            alert("删除会话失败，请稍后重试");
        }
    };

    // 退出登录
    const handleLogout = () => {
        clearAuth();
        router.push("/login");
    };

    // 页面加载时检查登录状态，加载历史
    useEffect(() => {
        const currentUser = getUser();
        if (!currentUser) {
            router.push("/login");
            return;
        }
        setUser(currentUser);

        // 从 localStorage 获取 thread_id，不存在则生成新的
        let storedThreadId = localStorage.getItem("thread_id");
        if (!storedThreadId) {
            storedThreadId = generateUUID();
            localStorage.setItem("thread_id", storedThreadId);
        }
        setThreadId(storedThreadId);
        loadHistory(storedThreadId);
        refreshSessions();
        setAuthChecked(true);
    }, []);

    // 新建会话：只生成新的 thread_id，不清空旧会话数据
    const handleNewChat = () => {
        const newThreadId = generateUUID();
        localStorage.setItem("thread_id", newThreadId);
        setThreadId(newThreadId);
        setMessages([]);
        messageIdCounter.current = 0;
    };

    // 滚动到底部
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages]);

    // 添加消息
    const addMessage = (message: Omit<Message, "id" | "timestamp">) => {
        messageIdCounter.current += 1;
        const newMessage: Message = {
            ...message,
            id: `msg_${messageIdCounter.current}_${Date.now()}`,
            timestamp: Date.now(),
        };
        setMessages((prev) => [...prev, newMessage]);
        return newMessage;
    };

    // 处理发送消息
    const handleSend = async (text: string, file?: File) => {
        if (processing) return;

        let imageUrl: string | undefined;

        // 如果有图片，先上传到 OSS
        if (file) {
            try {
                imageUrl = await uploadImageToOss(file);
            } catch (error) {
                console.error("图片上传失败:", error);
                addMessage({
                    role: "assistant",
                    content: "图片上传失败，请稍后重试。",
                });
                return;
            }
        }

        // 添加用户消息
        addMessage({
            role: "user",
            content: text || "上传了一张食材图片",
            imageUrl,
        });

        setProcessing(true);

        // 添加助手消息（流式输出）
        const assistantMessageId = addMessage({
            role: "assistant",
            content: "",
            streaming: true,
        }).id;

        try {
            await streamChat(
                text || "这是我冰箱里的食物，帮我看看能做什么佳肴？",
                (chunk) => {
                    // 更新消息内容
                    setMessages((prev) =>
                        prev.map((msg) =>
                            msg.id === assistantMessageId
                                ? { ...msg, content: msg.content + chunk }
                                : msg
                        )
                    );
                }, imageUrl,
                (error) => {
                    console.error("聊天失败:", error);
                    setMessages((prev) =>
                        prev.map((msg) =>
                            msg.id === assistantMessageId
                                ? {
                                    ...msg,
                                    content: msg.content + `\n[错误]: ${error.message}`,
                                    streaming: false,
                                }
                                : msg
                        )
                    );
                },
                () => {
                    // 流式输出完成
                    setMessages((prev) =>
                        prev.map((msg) =>
                            msg.id === assistantMessageId
                                ? { ...msg, streaming: false }
                                : msg
                        )
                    );
                    // 刷新会话列表（本次对话会创建/更新会话记录）
                    refreshSessions();
                },
                threadId
            );
        } finally {
            setProcessing(false);
        }
    };

    // 登录状态检查中
    if (!authChecked) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-amber-50 via-orange-50 to-red-50">
                <div className="text-gray-400 animate-pulse">加载中...</div>
            </div>
        );
    }

    return (
        <div className="min-h-screen relative">
            {/* 背景 */}
            <div className="fixed inset-0 bg-gradient-to-br from-amber-50 via-orange-50 to-red-50" />
            <div className="fixed inset-0 opacity-30">
                <div className="absolute top-20 left-10 w-72 h-72 bg-orange-200 rounded-full mix-blend-multiply filter blur-xl animate-pulse" />
                <div className="absolute top-40 right-10 w-96 h-96 bg-amber-200 rounded-full mix-blend-multiply filter blur-xl animate-pulse" style={{ animationDelay: '1s' }} />
                <div className="absolute bottom-20 left-1/3 w-80 h-80 bg-red-100 rounded-full mix-blend-multiply filter blur-xl animate-pulse" style={{ animationDelay: '2s' }} />
            </div>

            {/* 固定顶部标题栏 */}
            <header className="fixed top-0 left-0 right-0 z-50 p-4">
                <div className="max-w-4xl mx-auto bg-white/80 backdrop-blur-sm rounded-2xl shadow-lg border border-white/50 px-6 py-4 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-gradient-to-br from-orange-500 to-red-500 rounded-xl">
                            <ChefHat className="text-white" size={24} />
                        </div>
                        <div>
                            <h1 className="text-xl font-bold bg-gradient-to-r from-orange-600 to-red-600 bg-clip-text text-transparent">AI 私人厨师</h1>
                            <p className="text-sm text-gray-500">上传食材图片，获取个性化食谱推荐</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <SessionList
                            sessions={sessions}
                            currentThreadId={threadId}
                            onSelect={handleSelectSession}
                            onDelete={handleDeleteSession}
                        />
                        <button
                            onClick={handleNewChat}
                            className="flex items-center gap-2 px-4 py-2 bg-orange-500 hover:bg-orange-600 text-white rounded-xl transition-colors"
                        >
                            <Plus size={18} />
                            <span className="hidden sm:inline">新建会话</span>
                        </button>
                        {user && (
                            <div className="flex items-center gap-2 pl-2 ml-1 border-l border-orange-100">
                                <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-orange-50 rounded-lg">
                                    <span className="text-sm text-orange-700 font-medium">{user.username}</span>
                                </div>
                                <button
                                    onClick={handleLogout}
                                    className="p-2 text-gray-400 hover:text-red-500 transition-colors"
                                    title="退出登录"
                                >
                                    <LogOut size={18} />
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </header>

            {/* 主内容区域 */}
            <div className="relative flex flex-col min-h-screen max-w-4xl mx-auto px-4 pt-24 pb-24">
                {/* 聊天区域 */}
                <div className="flex-1 bg-white/60 backdrop-blur-sm rounded-2xl shadow-lg border border-white/50 overflow-hidden flex flex-col">
                    <div className="flex-1 overflow-y-auto p-4">
                        {messages.length === 0 ? (
                            <div className="h-full flex flex-col items-center justify-center text-gray-400 mt-3">
                                <div className="p-4 bg-white/80 rounded-full mb-4">
                                    <UtensilsCrossed size={48} className="text-orange-400" />
                                </div>
                                <p className="text-lg font-medium text-gray-600">上传食材图片开始吧</p>
                                <p className="text-sm mt-2 text-gray-400">我会帮您识别食材并推荐食谱</p>
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {messages.map((message) => (
                                    <ChatMessage key={message.id} message={message} />
                                ))}
                                <div ref={messagesEndRef} />
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* 固定底部输入区域 */}
            <div className="fixed bottom-0 left-0 right-0 z-50 p-4">
                <div className="max-w-4xl mx-auto bg-white/80 backdrop-blur-sm rounded-2xl shadow-lg border border-white/50">
                    <ChatInput onSend={handleSend} disabled={processing} />
                </div>
            </div>
        </div>
    );
}