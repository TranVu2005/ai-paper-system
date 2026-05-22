import { Loader2, MessageSquare } from "lucide-react";
import { cleanAnswerText } from "@/utils/answerCleaner";

export default function AssistantMessage({ item }) {
  const answer = cleanAnswerText(item?.answer || "");
  const isError = item?.status === "error";

  return (
    <div className="rounded-lg border border-zinc-200 p-5">
      <div className="flex items-start gap-2">
        <MessageSquare className="mt-0.5 h-4 w-4 text-zinc-700" />
        <p className="font-medium">{item?.question}</p>
      </div>
      <p className={`mt-3 text-sm leading-6 ${isError ? "text-rose-600" : "text-zinc-600"}`}>
        {answer}
      </p>
      {item?.pending ? (
        <div className="mt-3 inline-flex items-center gap-2 text-xs text-zinc-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Đang xử lý
        </div>
      ) : null}
    </div>
  );
}

