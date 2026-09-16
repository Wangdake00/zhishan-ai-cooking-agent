/**
 * 聊天输入组件（支持多图上传 + 语音输入）
 */
import { useState, useRef, KeyboardEvent } from "react";
import { Image, Send, X, Mic, Square, Loader2 } from "lucide-react";
import { transcribeAudio } from "@/lib/api";

interface ChatInputProps {
    onSend: (text: string, files?: File[]) => void;
    disabled?: boolean;
}

const MAX_IMAGES = 6;
const MAX_RECORDING_MS = 60_000; // 60 秒自动停止

/**
 * 将 MediaRecorder 产出的 webm/opus Blob 转换为 16kHz 单声道 WAV Blob。
 * DashScope Paraformer 要求 wav 格式（PCM 编码），不支持 webm 容器。
 */
async function webmBlobToWavFile(blob: Blob): Promise<File> {
    const arrayBuffer = await blob.arrayBuffer();

    // 用默认采样率的 AudioContext 解码 webm
    const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
    const audioCtx = new AudioCtx();
    const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
    audioCtx.close();

    // 用 OfflineAudioContext 重采样到 16kHz 单声道
    const targetRate = 16000;
    const offlineCtx = new OfflineAudioContext(
        1,
        Math.ceil(audioBuffer.duration * targetRate),
        targetRate,
    );
    const src = offlineCtx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(offlineCtx.destination);
    src.start();
    const rendered = await offlineCtx.startRendering();

    // 取单声道数据
    const channelData = rendered.getChannelData(0);

    // 编码 WAV（16-bit PCM）
    const wavBuffer = encodeWav(channelData, targetRate);
    return new File([wavBuffer], "recording.wav", { type: "audio/wav" });
}

/**
 * Float32 PCM → 16-bit PCM WAV Blob
 */
function encodeWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);

    const writeString = (offset: number, str: string) => {
        for (let i = 0; i < str.length; i++) {
            view.setUint8(offset + i, str.charCodeAt(i));
        }
    };

    // RIFF header
    writeString(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, "WAVE");

    // fmt chunk
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);       // chunk size
    view.setUint16(20, 1, true);        // PCM format
    view.setUint16(22, 1, true);        // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true); // byte rate
    view.setUint16(32, 2, true);        // block align
    view.setUint16(34, 16, true);        // bits per sample

    // data chunk
    writeString(36, "data");
    view.setUint32(40, samples.length * 2, true);

    // float32 → int16
    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        offset += 2;
    }

    return buffer;
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
    const [text, setText] = useState("");
    const [files, setFiles] = useState<File[]>([]);
    const [isRecording, setIsRecording] = useState(false);
    const [isTranscribing, setIsTranscribing] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const chunksRef = useRef<BlobPart[]>([]);
    const streamRef = useRef<MediaStream | null>(null);
    const recordingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const inputDisabled = disabled || isTranscribing;

    const handleSend = () => {
        if (text.trim() || files.length > 0) {
            onSend(text.trim(), files.length > 0 ? files : undefined);
            setText("");
            setFiles([]);
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
        const selectedFiles = Array.from(e.target.files || []);
        const validImages = selectedFiles.filter(f => f.type.startsWith("image/"));
        const remaining = MAX_IMAGES - files.length;
        const toAdd = validImages.slice(0, remaining);

        if (toAdd.length > 0) {
            setFiles(prev => [...prev, ...toAdd]);
        }

        if (fileInputRef.current) {
            fileInputRef.current.value = "";
        }
    };

    const removeFile = (index: number) => {
        setFiles(prev => prev.filter((_, i) => i !== index));
    };

    // ========== 语音录制 ==========

    const cleanupRecording = () => {
        if (recordingTimerRef.current) {
            clearTimeout(recordingTimerRef.current);
            recordingTimerRef.current = null;
        }
        if (streamRef.current) {
            streamRef.current.getTracks().forEach(t => t.stop());
            streamRef.current = null;
        }
        mediaRecorderRef.current = null;
        chunksRef.current = [];
    };

    const startRecording = async () => {
        if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
            alert("当前浏览器不支持语音录制，请使用 Chrome / Edge / Firefox 最新版");
            return;
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            streamRef.current = stream;

            const recorder = new MediaRecorder(stream);
            mediaRecorderRef.current = recorder;
            chunksRef.current = [];

            recorder.ondataavailable = (e) => {
                if (e.data.size > 0) {
                    chunksRef.current.push(e.data);
                }
            };

            recorder.onstop = async () => {
                // 必须先取出录音数据，cleanupRecording() 会把 chunksRef 清空
                const chunks = chunksRef.current;
                cleanupRecording();

                const blob = new Blob(chunks, { type: "audio/webm" });
                console.log("[voice] 录音停止，webm blob 大小:", blob.size, "bytes");
                if (blob.size === 0) {
                    setIsRecording(false);
                    alert("未采集到音频数据，请检查麦克风是否被其他程序占用");
                    return;
                }

                setIsRecording(false);
                setIsTranscribing(true);
                try {
                    const wavFile = await webmBlobToWavFile(blob);
                    console.log("[voice] 转换为 WAV:", wavFile.size, "bytes");
                    const transcribed = await transcribeAudio(wavFile);
                    console.log("[voice] 转写结果:", JSON.stringify(transcribed));
                    if (transcribed) {
                        setText(prev => prev ? `${prev} ${transcribed}` : transcribed);
                    } else {
                        alert("未识别到语音内容，请靠近麦克风重试");
                    }
                } catch (err) {
                    console.error("语音识别失败:", err);
                    alert(err instanceof Error ? err.message : "语音识别失败，请重试");
                } finally {
                    setIsTranscribing(false);
                }
            };

            recorder.start();
            setIsRecording(true);

            // 60 秒超时自动停止
            recordingTimerRef.current = setTimeout(() => {
                if (mediaRecorderRef.current?.state === "recording") {
                    mediaRecorderRef.current.stop();
                }
            }, MAX_RECORDING_MS);
        } catch (err) {
            cleanupRecording();
            setIsRecording(false);
            if (err instanceof DOMException && err.name === "NotAllowedError") {
                alert("麦克风权限被拒绝，请在浏览器设置中允许访问麦克风");
            } else {
                alert("无法访问麦克风: " + (err instanceof Error ? err.message : "未知错误"));
            }
        }
    };

    const stopRecording = () => {
        if (mediaRecorderRef.current?.state === "recording") {
            mediaRecorderRef.current.stop();
        }
    };

    const toggleRecording = () => {
        if (isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    };

    const placeholder = isTranscribing
        ? "语音识别中..."
        : files.length > 0
            ? `已选择${files.length}张图片，输入你的需求（如：推荐菜谱/计算热量）...`
            : "描述你有的食材或需求...";

    return (
        <div className="border-t border-gray-200/50 bg-white/80 p-4">
            {files.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-2">
                    {files.map((file, index) => (
                        <div key={index} className="relative">
                            <img
                                src={URL.createObjectURL(file)}
                                alt={`预览${index + 1}`}
                                className="max-w-20 max-h-20 rounded-lg object-cover"
                            />
                            <button
                                onClick={() => removeFile(index)}
                                className="absolute -top-2 -right-2 p-1 bg-red-500 text-white rounded-full hover:bg-red-600"
                            >
                                <X size={12} />
                            </button>
                            <div className="absolute bottom-0 left-0 right-0 bg-black/50 text-white text-xs text-center rounded-b-lg py-0.5">
                                {index + 1}
                            </div>
                        </div>
                    ))}
                    {files.length < MAX_IMAGES && (
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            className="flex items-center justify-center w-20 h-20 border-2 border-dashed border-gray-300 rounded-lg text-gray-400 hover:border-orange-400 hover:text-orange-500 transition-colors"
                        >
                            <Image size={20} />
                        </button>
                    )}
                </div>
            )}
            <div className="flex gap-2 items-center">
                <button
                    onClick={() => fileInputRef.current?.click()}
                    className="p-2.5 text-gray-500 hover:text-orange-500 hover:bg-orange-50 rounded-xl transition-colors"
                    disabled={inputDisabled || files.length >= MAX_IMAGES}
                    title={files.length >= MAX_IMAGES ? `最多上传${MAX_IMAGES}张` : "上传图片"}
                >
                    <Image size={20} />
                </button>
                <input
                    type="file"
                    ref={fileInputRef}
                    onChange={handleFileChange}
                    accept="image/*"
                    multiple
                    className="hidden"
                    disabled={inputDisabled}
                />
                {/* 麦克风按钮 */}
                <button
                    onClick={toggleRecording}
                    className={`p-2.5 rounded-xl transition-colors ${
                        isRecording
                            ? "text-red-500 bg-red-50 animate-pulse"
                            : "text-gray-500 hover:text-orange-500 hover:bg-orange-50"
                    }`}
                    disabled={inputDisabled}
                    title={isRecording ? "停止录音" : isTranscribing ? "识别中..." : "语音输入"}
                >
                    {isRecording ? (
                        <Square size={20} />
                    ) : isTranscribing ? (
                        <Loader2 size={20} className="animate-spin" />
                    ) : (
                        <Mic size={20} />
                    )}
                </button>
                <textarea
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={placeholder}
                    className="flex-1 bg-gray-100/80 border-0 rounded-2xl px-4 py-2.5 resize-none focus:outline-none focus:ring-2 focus:ring-orange-300 focus:bg-white transition-all"
                    rows={1}
                    disabled={inputDisabled}
                />
                <button
                    onClick={handleSend}
                    disabled={inputDisabled || isRecording || (!text.trim() && files.length === 0)}
                    className="p-2.5 bg-gradient-to-r from-orange-500 to-red-500 text-white rounded-xl hover:from-orange-600 hover:to-red-600 disabled:from-gray-300 disabled:to-gray-300 disabled:cursor-not-allowed transition-all shadow-md hover:shadow-lg"
                >
                    <Send size={20} />
                </button>
            </div>
            {files.length > 0 && (
                <p className="text-xs text-gray-400 mt-2">
                    已选 {files.length}/{MAX_IMAGES} 张图片 · 可继续添加或直接发送
                </p>
            )}
        </div>
    );
}
