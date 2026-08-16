/**
 * 聊天输入组件
 */
import {useState, useRef, KeyboardEvent} from "react";
import {Image, Send, X} from "lucide-react";

interface ChatInputProps {
    onSend: (text: string, file?: File) => void;
    disabled?: boolean;
}

export function ChatInput({onSend, disabled}: ChatInputProps) {
    const [text, setText] = useState("");
    const [file, setFile] = useState<File | undefined>();
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleSend = () => {
        if (text.trim() || file) {
            onSend(text.trim(), file);
            setText("");
            setFile(undefined);
            if (fileInputRef.current) {
                fileInputRef.current.value = "";
            }
        }
    };

    const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const selectedFile = e.target.files?.[0];
        if (selectedFile && selectedFile.type.startsWith("image/")) {
            setFile(selectedFile);
        }
    };

    return (
        <div className="border-t border-gray-200/50 bg-white/80 p-4">
            {file && (
                <div className="mb-3 flex items-center gap-2">
                    <div className="relative">
                        <img
                            src={URL.createObjectURL(file)}
                            alt="预览"
                            className="max-w-24 max-h-24 rounded-lg object-cover"
                        />
                        <button
                            onClick={() => setFile(undefined)}
                            className="absolute -top-2 -right-2 p-1 bg-red-500 text-white rounded-full hover:bg-red-600"
                        >
                            <X size={12}/>
                        </button>
                    </div>
                </div>
            )}
            <div className="flex gap-2 items-center">
                <button
                    onClick={() => fileInputRef.current?.click()}
                    className="p-2.5 text-gray-500 hover:text-orange-500 hover:bg-orange-50 rounded-xl transition-colors"
                    disabled={disabled}
                >
                    <Image size={20}/>
                </button>
                <input
                    type="file"
                    ref={fileInputRef}
                    onChange={handleFileChange}
                    accept="image/*"
                    className="hidden"
                    disabled={disabled}
                />
                <textarea
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="描述你有的食材..."
                    className="flex-1 bg-gray-100/80 border-0 rounded-2xl px-4 py-2.5 resize-none focus:outline-none focus:ring-2 focus:ring-orange-300 focus:bg-white transition-all"
                    rows={1}
                    disabled={disabled}
                />
                <button
                    onClick={handleSend}
                    disabled={disabled || (!text.trim() && !file)}
                    className="p-2.5 bg-gradient-to-r from-orange-500 to-red-500 text-white rounded-xl hover:from-orange-600 hover:to-red-600 disabled:from-gray-300 disabled:to-gray-300 disabled:cursor-not-allowed transition-all shadow-md hover:shadow-lg"
                >
                    <Send size={20}/>
                </button>
            </div>
        </div>
    );
}