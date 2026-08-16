/**
 * 会话列表组件（下拉式）
 * 点击按钮展开，列出所有历史会话，支持切换和删除
 */
import {useState, useRef, useEffect} from "react";
import {MessageSquare, Trash2, X, ChevronDown} from "lucide-react";
import {Session} from "@/types/chat";

interface SessionListProps {
    sessions: Session[];
    currentThreadId: string;
    onSelect: (threadId: string) => void;
    onDelete: (threadId: string) => void;
}

export function SessionList({sessions, currentThreadId, onSelect, onDelete}: SessionListProps) {
    const [open, setOpen] = useState(false);
    const dropdownRef = useRef<HTMLDivElement>(null);

    // 点击外部关闭下拉
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
                setOpen(false);
            }
        };
        if (open) {
            document.addEventListener("mousedown", handleClickOutside);
        }
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, [open]);

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

    return (
        <div className="relative" ref={dropdownRef}>
            <button
                onClick={() => setOpen(!open)}
                className="flex items-center gap-2 px-4 py-2 bg-white/60 hover:bg-orange-50 text-gray-700 rounded-xl transition-colors border border-orange-100"
                title="历史会话"
            >
                <MessageSquare size={18}/>
                <span className="hidden sm:inline">历史会话</span>
                <ChevronDown size={14} className={`transition-transform ${open ? "rotate-180" : ""}`}/>
            </button>

            {open && (
                <div className="absolute right-0 top-full mt-2 w-80 max-h-96 overflow-y-auto bg-white rounded-2xl shadow-xl border border-gray-100 z-50">
                    <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 sticky top-0 bg-white">
                        <h3 className="font-semibold text-gray-800">历史会话</h3>
                        <button
                            onClick={() => setOpen(false)}
                            className="text-gray-400 hover:text-gray-600"
                        >
                            <X size={16}/>
                        </button>
                    </div>

                    {sessions.length === 0 ? (
                        <div className="px-4 py-8 text-center text-gray-400 text-sm">
                            还没有历史会话
                        </div>
                    ) : (
                        <div className="py-1">
                            {sessions.map((session) => {
                                const isActive = session.thread_id === currentThreadId;
                                return (
                                    <div
                                        key={session.thread_id}
                                        className={`group flex items-center gap-2 px-3 py-2.5 mx-1 my-0.5 rounded-xl cursor-pointer transition-colors ${
                                            isActive
                                                ? "bg-orange-50 border border-orange-200"
                                                : "hover:bg-gray-50"
                                        }`}
                                        onClick={() => {
                                            onSelect(session.thread_id);
                                            setOpen(false);
                                        }}
                                    >
                                        <MessageSquare
                                            size={14}
                                            className={`flex-shrink-0 ${isActive ? "text-orange-500" : "text-gray-400"}`}
                                        />
                                        <div className="flex-1 min-w-0">
                                            <p className={`text-sm truncate ${isActive ? "text-orange-700 font-medium" : "text-gray-700"}`}>
                                                {session.title || "新会话"}
                                            </p>
                                            <p className="text-xs text-gray-400">{formatTime(session.updated_at)}</p>
                                        </div>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                onDelete(session.thread_id);
                                            }}
                                            className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition-all"
                                            title="删除会话"
                                        >
                                            <Trash2 size={14}/>
                                        </button>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
