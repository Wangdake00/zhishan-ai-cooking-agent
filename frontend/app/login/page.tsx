"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ChefHat, User, Lock, Loader2, CheckCircle2 } from "lucide-react";
import { login, register } from "@/lib/api";
import { setToken, setUser } from "@/lib/auth";

export default function LoginPage() {
    const router = useRouter();
    const [isLogin, setIsLogin] = useState(true);
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [success, setSuccess] = useState("");

    const switchMode = () => {
        setIsLogin(!isLogin);
        setError("");
        setSuccess("");
        setConfirmPassword("");
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (loading) return;

        setError("");
        setSuccess("");
        setLoading(true);

        try {
            if (isLogin) {
                // 登录：成功后跳转首页
                const { token, user } = await login(username, password);
                setToken(token);
                setUser(user);
                router.push("/");
            } else {
                // 注册：先验证两次密码一致
                if (password !== confirmPassword) {
                    setError("两次输入的密码不一致");
                    return;
                }
                // 注册成功后切回登录，不自动登录
                await register(username, password);
                setSuccess("注册成功，请登录");
                setIsLogin(true);
                setPassword("");
                setConfirmPassword("");
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : "操作失败");
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen relative flex items-center justify-center px-4">
            {/* 背景 */}
            <div className="fixed inset-0 bg-gradient-to-br from-amber-50 via-orange-50 to-red-50" />
            <div className="fixed inset-0 opacity-30">
                <div className="absolute top-20 left-10 w-72 h-72 bg-orange-200 rounded-full mix-blend-multiply filter blur-xl animate-pulse" />
                <div className="absolute top-40 right-10 w-96 h-96 bg-amber-200 rounded-full mix-blend-multiply filter blur-xl animate-pulse" style={{ animationDelay: "1s" }} />
                <div className="absolute bottom-20 left-1/3 w-80 h-80 bg-red-100 rounded-full mix-blend-multiply filter blur-xl animate-pulse" style={{ animationDelay: "2s" }} />
            </div>

            {/* 登录卡片 */}
            <div className="relative w-full max-w-md">
                <div className="bg-white/80 backdrop-blur-sm rounded-3xl shadow-xl border border-white/50 p-8">
                    {/* Logo */}
                    <div className="flex flex-col items-center mb-8">
                        <div className="p-3 bg-gradient-to-br from-orange-500 to-red-500 rounded-2xl mb-4">
                            <ChefHat className="text-white" size={32} />
                        </div>
                        <h1 className="text-2xl font-bold bg-gradient-to-r from-orange-600 to-red-600 bg-clip-text text-transparent">
                            AI 私人厨师
                        </h1>
                        <p className="text-sm text-gray-500 mt-1">
                            {isLogin ? "欢迎回来，请登录" : "创建账号，开启美食之旅"}
                        </p>
                    </div>

                    {/* 成功提示 */}
                    {success && (
                        <div className="mb-4 flex items-center gap-2 px-4 py-2.5 bg-green-50 border border-green-200 rounded-xl text-sm text-green-600">
                            <CheckCircle2 size={16} />
                            {success}
                        </div>
                    )}

                    {/* 错误提示 */}
                    {error && (
                        <div className="mb-4 px-4 py-2.5 bg-red-50 border border-red-200 rounded-xl text-sm text-red-600">
                            {error}
                        </div>
                    )}

                    {/* 表单 */}
                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1.5">用户名</label>
                            <div className="relative">
                                <User className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
                                <input
                                    type="text"
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    placeholder="2-20 个字符"
                                    required
                                    minLength={2}
                                    maxLength={20}
                                    className="w-full pl-10 pr-4 py-2.5 bg-white/60 border border-orange-100 rounded-xl focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent transition-all"
                                />
                            </div>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1.5">密码</label>
                            <div className="relative">
                                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
                                <input
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    placeholder="至少 6 个字符"
                                    required
                                    minLength={6}
                                    maxLength={64}
                                    className="w-full pl-10 pr-4 py-2.5 bg-white/60 border border-orange-100 rounded-xl focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent transition-all"
                                />
                            </div>
                        </div>

                        {/* 确认密码（仅注册时显示） */}
                        {!isLogin && (
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1.5">确认密码</label>
                                <div className="relative">
                                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
                                    <input
                                        type="password"
                                        value={confirmPassword}
                                        onChange={(e) => setConfirmPassword(e.target.value)}
                                        placeholder="再次输入密码"
                                        required
                                        minLength={6}
                                        maxLength={64}
                                        className="w-full pl-10 pr-4 py-2.5 bg-white/60 border border-orange-100 rounded-xl focus:outline-none focus:ring-2 focus:ring-orange-400 focus:border-transparent transition-all"
                                    />
                                </div>
                            </div>
                        )}

                        {/* 提交按钮 */}
                        <button
                            type="submit"
                            disabled={loading}
                            className="w-full flex items-center justify-center gap-2 py-2.5 bg-orange-500 hover:bg-orange-600 disabled:bg-orange-300 text-white font-medium rounded-xl transition-colors"
                        >
                            {loading && <Loader2 className="animate-spin" size={18} />}
                            {loading ? "处理中..." : isLogin ? "登录" : "注册"}
                        </button>
                    </form>

                    {/* 切换登录/注册 */}
                    <div className="mt-6 text-center text-sm text-gray-500">
                        {isLogin ? "还没有账号？" : "已有账号？"}
                        <button
                            onClick={switchMode}
                            className="ml-1 text-orange-600 hover:text-orange-700 font-medium"
                        >
                            {isLogin ? "去注册" : "去登录"}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
