import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

export default function RenameSessionDialog({ open, workspace, loading, onClose, onSave }) {
  const [title, setTitle] = useState("");

  useEffect(() => {
    setTitle(String(workspace?.title || ""));
  }, [workspace]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const clean = title.trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold">Sửa tên phiên làm việc</h3>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mt-4 h-11 w-full rounded-lg border border-zinc-300 px-3 text-sm outline-none focus:border-zinc-700"
          placeholder="Nhập tên mới"
        />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={loading}>Hủy</Button>
          <Button onClick={() => onSave(clean)} disabled={loading || !clean}>
            {loading ? "Đang lưu..." : "Lưu"}
          </Button>
        </div>
      </div>
    </div>
  );
}

