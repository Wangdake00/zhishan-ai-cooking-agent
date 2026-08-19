"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { Message, Session, Favorite } from "@/types/chat";
import { ChatMessage } from "@/components/ChatMessage";
import { ChatInput } from "@/components/ChatInput";
import { uploadImageToOss, streamChat, getChatHistory, listSessions, deleteSession, listFavorites, deleteFavorite } from "@/lib/api";
import { generateUUID } from "@/lib/utils";
import { getUser, clearAuth } from "@/lib/auth";
import type { User } from "@/lib/auth";
import {
    UtensilsCrossed,
    ChefHat,
    Plus,
    LogOut,
    MessageSquare,
    Trash2,
    X,
    Heart,
    Clock,
    ChevronRight,
    User as UserIcon,
    Sparkles,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type SidebarTab = "sessions" | "favorites";

// React 19 purity lint 规则误判 Date.now() 为「render 中调用不纯函数」，这里包一层 helper 放顶层避开
const getTimestamp = () => Date.now();

export default function Home() {
    const router = useRouter();
    const [messages, setMessages] = useState<Message[]>([]);
    const [processing, setProcessing] = useState(false);
    // SSR 阶段 localStorage / window 不存在，不能在初始化里访问，否则会抛 "localStorage is not defined"
    // 先用安全的空值占位，等客户端挂载后再同步
    const [threadId, setThreadId] = useState<string>("");
    const [sessions, setSessions] = useState<Session[]>([]);
    const [favorites, setFavorites] = useState<Favorite[]>([]);
    // user 同上：SSR 时返回 null，客户端 mount 后再读 localStorage 同步
    const [user, setUser] = useState<User | null>(null);
    const [authChecked, setAuthChecked] = useState(false);
    const [sidebarOpen, setSidebarOpen] = useState(true); // 默认展开
    const [sidebarTab, setSidebarTab] = useState<SidebarTab>("sessions");
    const [selectedFavorite, setSelectedFavorite] = useState<Favorite | null>(null);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const messageIdCounter = useRef(0);

    // 格式化时间显示
    const formatTime = (iso: string) => {
        try {
            const date = new Date(iso);
            const now = new Date();
            const diff = now.getTime() - date.getTime();
            const minutes = Math.floor(diff / 60000);
            const hours = Math.floor(diff / 3600000);
            const days = Math.floor(diff / 86400000);
            if (minutes < 1) return "刚刚";
            if (minutes < 60) return `${minutes} 分钟前`;
            if (hours < 24) return `${hours} 小时前`;
            if (days < 7) return `${days} 天前`;
            return date.toLocaleDateString("zh-CN");
        } catch {
            return "";
        }
    };

    // 加载历史消息
    const loadHistory = async (id: string) => {
        try {
            setSelectedFavorite(null);
            const history = await getChatHistory(id);
            if (history && history.length > 0) {
                const loadedMessages: Message[] = history.map((msg, index) => {
                    let content = "";
                    let imageUrl: string | undefined;

                    if (typeof msg.content === 'string') {
                        content = msg.content;
                    } else if (Array.isArray(msg.content)) {
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

    // 刷新收藏列表
    const refreshFavorites = async () => {
        try {
            const list = await listFavorites();
            setFavorites(list);
        } catch (error) {
            console.error("获取收藏列表失败:", error);
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
            setSessions((prev) => prev.filter((s) => s.thread_id !== id));
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

    // 查看收藏详情
    const handleSelectFavorite = async (fav: Favorite) => {
        setSelectedFavorite(fav);
    };

    // 删除收藏
    const handleDeleteFavorite = async (favId: number, e?: React.MouseEvent) => {
        e?.stopPropagation();
        const confirmDelete = window.confirm("确定要删除这个收藏吗？");
        if (!confirmDelete) return;
        try {
            await deleteFavorite(favId);
            setFavorites((prev) => prev.filter((f) => f.id !== favId));
            if (selectedFavorite?.id === favId) {
                setSelectedFavorite(null);
            }
        } catch (error) {
            console.error("删除收藏失败:", error);
            alert("删除收藏失败");
        }
    };

    // 退出登录
    const handleLogout = () => {
        clearAuth();
        router.push("/login");
    };

    // 客户端挂载后：从 localStorage 同步 user / threadId，加载历史数据
    // （故意只在客户端执行，避免 SSR 阶段访问不存在的 localStorage / window）
    useEffect(() => {
        /* eslint-disable react-hooks/set-state-in-effect */
        const currentUser = getUser();
        if (!currentUser) {
            router.push("/login");
            return;
        }
        setUser(currentUser);

        // 同步 thread_id：localStorage 里有的话复用，没有则新建一个
        let storedThreadId = localStorage.getItem("thread_id");
        if (!storedThreadId) {
            storedThreadId = generateUUID();
            localStorage.setItem("thread_id", storedThreadId);
        }
        setThreadId(storedThreadId);

        loadHistory(storedThreadId);
        refreshSessions();
        refreshFavorites();
        setAuthChecked(true);
        /* eslint-enable react-hooks/set-state-in-effect */
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // 新建会话
    const handleNewChat = () => {
        const newThreadId = generateUUID();
        localStorage.setItem("thread_id", newThreadId);
        setThreadId(newThreadId);
        setMessages([]);
        setSelectedFavorite(null);
        messageIdCounter.current = 0;
    };

    // 滚动到底部
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, selectedFavorite]);

    // 添加消息
    const addMessage = (message: Omit<Message, "id" | "timestamp">) => {
        messageIdCounter.current += 1;
        const ts = getTimestamp();
        const newMessage: Message = {
            ...message,
            id: `msg_${messageIdCounter.current}_${ts}`,
            timestamp: ts,
        };
        setMessages((prev) => [...prev, newMessage]);
        return newMessage;
    };

    // 处理发送消息（支持多图）
    const handleSend = async (text: string, files?: File[]) => {
        if (processing) return;

        let imageUrls: string[] = [];
        if (files && files.length > 0) {
            try {
                // 批量上传图片到 OSS
                const uploadPromises = files.map(f => uploadImageToOss(f));
                imageUrls = await Promise.all(uploadPromises);
            } catch (error) {
                console.error("图片上传失败:", error);
                addMessage({
                    role: "assistant",
                    content: "部分图片上传失败，请重试或减少图片数量。",
                });
                return;
            }
        }

        // 切换回聊天视图（如果正在看收藏）
        setSelectedFavorite(null);

        addMessage({
            role: "user",
            content: text || (imageUrls.length > 0 ? `上传了 ${imageUrls.length} 张图片` : "你好"),
            imageUrls: imageUrls.length > 0 ? imageUrls : undefined,
            imageUrl: imageUrls.length === 1 ? imageUrls[0] : undefined, // 兼容单图
        });

        setProcessing(true);

        const assistantMessageId = addMessage({
            role: "assistant",
            content: "",
            streaming: true,
        }).id;

        try {
            await streamChat(
                text || "",
                (chunk) => {
                    setMessages((prev) =>
                        prev.map((msg) =>
                            msg.id === assistantMessageId
                                ? { ...msg, content: msg.content + chunk }
                                : msg
                        )
                    );
                },
                imageUrls.length > 0 ? imageUrls : undefined,
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
                    setMessages((prev) =>
                        prev.map((msg) =>
                            msg.id === assistantMessageId
                                ? { ...msg, streaming: false }
                                : msg
                        )
                    );
                    refreshSessions();
                    setTimeout(refreshFavorites, 300);
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
        <div className="min-h-screen bg-gradient-to-br from-amber-50 via-orange-50 to-red-50 flex">
            {/* ============ 左侧弹出式侧边栏 ============ */}
            <aside
                className={`fixed lg:static inset-y-0 left-0 z-40 w-72 bg-white/90 backdrop-blur-md shadow-xl border-r border-orange-100 flex flex-col transform transition-transform duration-300 ease-in-out ${sidebarOpen ? "translate-x-0" : "-translate-x-full lg:-translate-x-full"
                    }`}
            >
                {/* 侧边栏头部：Logo + 关闭按钮 */}
                <div className="flex items-center justify-between p-4 border-b border-orange-100">
                    <div className="flex items-center gap-2">
                        <div className="p-2 bg-gradient-to-br from-orange-500 to-red-500 rounded-xl">
                            <ChefHat className="text-white" size={20} />
                        </div>
                        <div>
                            <h1 className="text-base font-bold bg-gradient-to-r from-orange-600 to-red-600 bg-clip-text text-transparent">
                                AI 私人厨师
                            </h1>
                            <p className="text-xs text-gray-400">您的专属烹饪助手</p>
                        </div>
                    </div>
                    <button
                        onClick={() => setSidebarOpen(false)}
                        className="lg:hidden p-1.5 rounded-lg hover:bg-orange-50 text-gray-500 hover:text-orange-600 transition-colors"
                    >
                        <X size={18} />
                    </button>
                </div>

                {/* 新建会话按钮 */}
                <div className="p-3">
                    <button
                        onClick={handleNewChat}
                        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-600 hover:to-red-600 text-white rounded-xl transition-all shadow-md hover:shadow-lg"
                    >
                        <Plus size={18} />
                        <span className="font-medium">新建会话</span>
                    </button>
                </div>

                {/* Tab 切换 */}
                <div className="px-3 mb-2">
                    <div className="flex bg-orange-50 rounded-xl p-1">
                        <button
                            onClick={() => setSidebarTab("sessions")}
                            className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${sidebarTab === "sessions"
                                ? "bg-white text-orange-600 shadow-sm"
                                : "text-gray-500 hover:text-gray-700"
                                }`}
                        >
                            <MessageSquare size={15} />
                            <span>历史会话</span>
                        </button>
                        <button
                            onClick={() => setSidebarTab("favorites")}
                            className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${sidebarTab === "favorites"
                                ? "bg-white text-orange-600 shadow-sm"
                                : "text-gray-500 hover:text-gray-700"
                                }`}
                        >
                            <Heart size={15} />
                            <span>收藏食谱</span>
                        </button>
                    </div>
                </div>

                {/* 列表内容区 */}
                <div className="flex-1 overflow-y-auto px-2 pb-2">
                    {sidebarTab === "sessions" ? (
                        // === 历史会话列表 ===
                        <div className="space-y-1">
                            {sessions.length === 0 ? (
                                <div className="text-center py-10 text-gray-400">
                                    <MessageSquare size={32} className="mx-auto mb-2 opacity-40" />
                                    <p className="text-sm">还没有历史会话</p>
                                    <p className="text-xs mt-1">开始新的对话吧</p>
                                </div>
                            ) : (
                                sessions.map((session) => {
                                    const isActive = session.thread_id === threadId && !selectedFavorite;
                                    return (
                                        <div
                                            key={session.thread_id}
                                            className={`group flex items-center gap-2 px-3 py-2.5 rounded-xl cursor-pointer transition-colors ${isActive
                                                ? "bg-orange-50 border border-orange-200"
                                                : "hover:bg-gray-50 border border-transparent"
                                                }`}
                                            onClick={() => handleSelectSession(session.thread_id)}
                                        >
                                            <MessageSquare
                                                size={15}
                                                className={`flex-shrink-0 ${isActive ? "text-orange-500" : "text-gray-400"}`}
                                            />
                                            <div className="flex-1 min-w-0">
                                                <p className={`text-sm truncate font-medium ${isActive ? "text-orange-700" : "text-gray-700"}`}>
                                                    {session.title || "新会话"}
                                                </p>
                                                <p className="text-xs text-gray-400 flex items-center gap-1 mt-0.5">
                                                    <Clock size={10} />
                                                    {formatTime(session.updated_at)}
                                                </p>
                                            </div>
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleDeleteSession(session.thread_id);
                                                }}
                                                className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition-all rounded-md hover:bg-red-50"
                                                title="删除会话"
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    );
                                })
                            )}
                        </div>
                    ) : (
                        // === 收藏食谱列表 ===
                        <div className="space-y-1">
                            {favorites.length === 0 ? (
                                <div className="text-center py-10 text-gray-400">
                                    <Heart size={32} className="mx-auto mb-2 opacity-40" />
                                    <p className="text-sm">还没有收藏的食谱</p>
                                    <p className="text-xs mt-1">在对话中说 &quot;收藏这个食谱&quot;</p>
                                </div>
                            ) : (
                                favorites.map((fav) => {
                                    const isActive = selectedFavorite?.id === fav.id;
                                    return (
                                        <div
                                            key={fav.id}
                                            className={`group flex items-center gap-2 px-3 py-2.5 rounded-xl cursor-pointer transition-colors ${isActive
                                                ? "bg-red-50 border border-red-200"
                                                : "hover:bg-gray-50 border border-transparent"
                                                }`}
                                            onClick={() => handleSelectFavorite(fav)}
                                        >
                                            <Heart
                                                size={15}
                                                className={`flex-shrink-0 ${isActive ? "text-red-500 fill-red-500" : "text-gray-400"}`}
                                            />
                                            <div className="flex-1 min-w-0">
                                                <p className={`text-sm truncate font-medium ${isActive ? "text-red-700" : "text-gray-700"}`}>
                                                    {fav.title}
                                                </p>
                                                <p className="text-xs text-gray-400 flex items-center gap-1 mt-0.5">
                                                    <Clock size={10} />
                                                    {formatTime(fav.created_at)}
                                                </p>
                                            </div>
                                            <button
                                                onClick={(e) => handleDeleteFavorite(fav.id, e)}
                                                className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition-all rounded-md hover:bg-red-50"
                                                title="删除收藏"
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    );
                                })
                            )}
                        </div>
                    )}
                </div>

                {/* ===== 底部用户信息区 ===== */}
                <div className="border-t border-orange-100 bg-gradient-to-t from-orange-50/80 to-transparent p-3">
                    {user && (
                        <div className="flex items-center gap-3 p-2 rounded-xl hover:bg-white/60 transition-colors group">
                            {/* 用户头像 */}
                            <div className="relative flex-shrink-0">
                                <div className="w-10 h-10 rounded-full bg-gradient-to-br from-orange-400 to-red-500 flex items-center justify-center shadow-md">
                                    <UserIcon size={18} className="text-white" />
                                </div>
                                <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 bg-green-400 rounded-full border-2 border-white" />
                            </div>
                            {/* 用户名字 */}
                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-semibold text-gray-800 truncate">{user.username}</p>
                                <p className="text-xs text-gray-500 flex items-center gap-1">
                                    <Sparkles size={10} className="text-orange-400" />
                                    AI 私厨会员
                                </p>
                            </div>
                            {/* 退出按钮 */}
                            <button
                                onClick={handleLogout}
                                className="p-2 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors opacity-0 group-hover:opacity-100"
                                title="退出登录"
                            >
                                <LogOut size={16} />
                            </button>
                        </div>
                    )}
                </div>
            </aside>

            {/* 侧边栏遮罩（移动端） */}
            {sidebarOpen && (
                <div
                    className="fixed inset-0 z-30 bg-black/30 backdrop-blur-sm lg:hidden"
                    onClick={() => setSidebarOpen(false)}
                />
            )}

            {/* ============ 主聊天区域 ============ */}
            <main className="flex-1 flex flex-col min-w-0 relative">
                {/* 顶部工具栏 */}
                <header className="sticky top-0 z-20 px-4 lg:px-6 py-3 bg-white/60 backdrop-blur-md border-b border-orange-100">
                    <div className="flex items-center justify-end">
                        {/* 工具快捷提示 */}
                        <div className="hidden md:flex items-center gap-2 text-xs">
                            {[
                                { icon: "🔍", label: "搜索食谱" },
                                { icon: "📊", label: "营养分析" },
                                { icon: "🛒", label: "购物清单" },
                                { icon: "❤️", label: "收藏食谱" },
                            ].map((t) => (
                                <span
                                    key={t.label}
                                    className="flex items-center gap-1 px-2.5 py-1 bg-orange-50 text-orange-700 rounded-full border border-orange-100"
                                >
                                    <span>{t.icon}</span>
                                    <span className="font-medium">{t.label}</span>
                                </span>
                            ))}
                        </div>
                    </div>
                </header>

                {/* 聊天/收藏内容区 */}
                <div className="flex-1 overflow-y-auto px-4 lg:px-8 py-6">
                    <div className="max-w-4xl mx-auto">
                        {selectedFavorite ? (
                            // ==== 收藏食谱详情视图 ====
                            <div className="bg-white/80 backdrop-blur-sm rounded-2xl shadow-lg border border-white/80 overflow-hidden">
                                <div className="px-6 py-5 border-b border-orange-100 bg-gradient-to-r from-red-50 to-orange-50">
                                    <div className="flex items-start justify-between gap-4">
                                        <div className="flex items-start gap-3">
                                            <div className="p-2.5 bg-gradient-to-br from-red-400 to-pink-500 rounded-xl shadow-md">
                                                <Heart className="text-white fill-white" size={22} />
                                            </div>
                                            <div>
                                                <h2 className="text-xl font-bold text-gray-800">{selectedFavorite.title}</h2>
                                                <p className="text-sm text-gray-500 mt-1">
                                                    收藏于 {new Date(selectedFavorite.created_at).toLocaleString("zh-CN")}
                                                </p>
                                                {selectedFavorite.ingredients && (
                                                    <p className="text-sm text-orange-600 mt-2 flex items-center gap-1 flex-wrap">
                                                        <span className="font-medium">🥗 主要食材：</span>
                                                        <span>{selectedFavorite.ingredients}</span>
                                                    </p>
                                                )}
                                            </div>
                                        </div>
                                        <button
                                            onClick={() => setSelectedFavorite(null)}
                                            className="p-2 rounded-lg hover:bg-white text-gray-500 hover:text-gray-700 transition-colors"
                                            title="关闭"
                                        >
                                            <X size={18} />
                                        </button>
                                    </div>
                                </div>
                                <div className="p-6">
                                    <div className="prose prose-orange max-w-none">
                                        <ReactMarkdown
                                            remarkPlugins={[remarkGfm]}
                                            components={{
                                                h1: ({ children }) => <h1 className="text-xl font-bold mb-3 mt-4 text-gray-800">{children}</h1>,
                                                h2: ({ children }) => <h2 className="text-lg font-bold mb-2 mt-4 text-gray-800">{children}</h2>,
                                                h3: ({ children }) => <h3 className="text-base font-semibold mb-2 mt-3 text-gray-700">{children}</h3>,
                                                p: ({ children }) => <p className="mb-3 last:mb-0 text-gray-700 leading-relaxed">{children}</p>,
                                                ul: ({ children }) => <ul className="list-disc ml-5 mb-3 space-y-1">{children}</ul>,
                                                ol: ({ children }) => <ol className="list-decimal ml-5 mb-3 space-y-1">{children}</ol>,
                                                li: ({ children }) => <li className="mb-1 leading-relaxed text-gray-700">{children}</li>,
                                                a: ({ href, children }) => <a href={href} className="text-blue-600 hover:underline" target="_blank" rel="noopener noreferrer">{children}</a>,
                                                strong: ({ children }) => <strong className="font-semibold text-gray-800">{children}</strong>,
                                                em: ({ children }) => <em className="italic">{children}</em>,
                                                code: ({ children }) => <code className="bg-orange-50 text-orange-700 px-1.5 py-0.5 rounded text-sm font-mono">{children}</code>,
                                                pre: ({ children }) => <pre className="bg-gray-50 p-4 rounded-xl overflow-x-auto mb-3 text-sm">{children}</pre>,
                                                blockquote: ({ children }) => <blockquote className="border-l-4 border-orange-300 pl-4 italic text-gray-600 mb-3 bg-orange-50/50 py-2 pr-3 rounded-r-lg">{children}</blockquote>,
                                                table: ({ children }) => <table className="w-full border-collapse border border-gray-200 mb-4 rounded-xl overflow-hidden">{children}</table>,
                                                thead: ({ children }) => <thead className="bg-orange-50">{children}</thead>,
                                                tbody: ({ children }) => <tbody>{children}</tbody>,
                                                tr: ({ children }) => <tr className="border-b border-gray-100 last:border-b-0">{children}</tr>,
                                                th: ({ children }) => <th className="border border-gray-200 px-3 py-2 text-left font-semibold text-gray-700 bg-orange-50">{children}</th>,
                                                td: ({ children }) => <td className="border border-gray-200 px-3 py-2 text-gray-700">{children}</td>,
                                            }}
                                        >
                                            {selectedFavorite.content}
                                        </ReactMarkdown>
                                    </div>
                                    {/* 操作按钮 */}
                                    <div className="mt-6 pt-5 border-t border-gray-100 flex flex-wrap gap-3">
                                        <button
                                            onClick={() => {
                                                setSelectedFavorite(null);
                                                handleSend(`参考这个食谱：${selectedFavorite.title}\n\n${selectedFavorite.content}`);
                                            }}
                                            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-600 hover:to-red-600 text-white rounded-xl transition-colors shadow-md"
                                        >
                                            <ChevronRight size={16} />
                                            <span>继续对话聊这个食谱</span>
                                        </button>
                                        <button
                                            onClick={(e) => handleDeleteFavorite(selectedFavorite.id, e)}
                                            className="flex items-center gap-2 px-4 py-2 border border-red-200 hover:bg-red-50 text-red-600 rounded-xl transition-colors"
                                        >
                                            <Trash2 size={16} />
                                            <span>删除收藏</span>
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ) : messages.length === 0 ? (
                            // ==== 空状态欢迎页 ====
                            <div className="h-full flex flex-col items-center justify-center py-16">
                                <div className="p-5 bg-white rounded-full shadow-lg mb-6 border border-orange-100">
                                    <UtensilsCrossed size={56} className="text-orange-400" />
                                </div>
                                <h2 className="text-2xl font-bold text-gray-700 mb-2">欢迎使用 AI 私人厨师</h2>
                                <p className="text-gray-500 mb-8">上传食材图片，或直接描述你有什么食材，我来帮你！</p>

                                {/* 功能卡片 */}
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 w-full max-w-2xl">
                                    {[
                                        {
                                            icon: "📷",
                                            title: "拍照识别食材",
                                            desc: "上传冰箱或食材照片，AI自动识别可用食材",
                                            prompt: "（点击下方图片按钮上传你的食材照片）"
                                        },
                                        {
                                            icon: "📊",
                                            title: "营养成分分析",
                                            desc: "计算卡路里、蛋白质、脂肪、碳水等营养",
                                            prompt: "鸡胸肉200g, 西兰花150g, 大米100g，帮我算一下营养"
                                        },
                                        {
                                            icon: "🛒",
                                            title: "智能购物清单",
                                            desc: "按食材分类自动整理购物清单，可勾选",
                                            prompt: "我要做3人份西红柿炒鸡蛋，帮我列购物清单"
                                        },
                                        {
                                            icon: "❤️",
                                            title: "收藏喜欢的食谱",
                                            desc: "一键收藏AI推荐的菜谱，随时回顾",
                                            prompt: "（对话中回复「收藏这个食谱」即可保存）"
                                        },
                                    ].map((item, idx) => (
                                        <button
                                            key={idx}
                                            onClick={() => {
                                                if (item.prompt.includes("点击下方")) return;
                                                if (item.prompt.includes("对话中回复")) return;
                                                handleSend(item.prompt);
                                            }}
                                            className="text-left p-4 bg-white/80 backdrop-blur-sm rounded-2xl border border-orange-100 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all group"
                                        >
                                            <div className="text-3xl mb-2">{item.icon}</div>
                                            <h3 className="font-semibold text-gray-800 group-hover:text-orange-600 transition-colors">
                                                {item.title}
                                            </h3>
                                            <p className="text-sm text-gray-500 mt-1">{item.desc}</p>
                                            {!item.prompt.includes("点击下方") && !item.prompt.includes("对话中回复") && (
                                                <p className="text-xs text-orange-500 mt-2 bg-orange-50 px-2 py-1 rounded-lg inline-block">
                                                    💡 {item.prompt.slice(0, 24)}...
                                                </p>
                                            )}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        ) : (
                            // ==== 聊天消息列表 ====
                            <div className="space-y-5">
                                {messages.map((message) => (
                                    <ChatMessage key={message.id} message={message} />
                                ))}
                                <div ref={messagesEndRef} />
                            </div>
                        )}
                    </div>
                </div>

                {/* 固定底部输入区域 */}
                <div className="sticky bottom-0 z-10 px-4 lg:px-8 py-4 bg-gradient-to-t from-amber-50 via-amber-50/95 to-transparent">
                    <div className="max-w-4xl mx-auto">
                        <div className="bg-white/90 backdrop-blur-sm rounded-2xl shadow-lg border border-white/70 p-2">
                            <ChatInput onSend={handleSend} disabled={processing} />
                        </div>
                        <p className="text-center text-xs text-gray-400 mt-2">
                            AI 生成的内容仅供参考 · 食材识别结果请以实际为准
                        </p>
                    </div>
                </div>
            </main>
        </div>
    );
}
