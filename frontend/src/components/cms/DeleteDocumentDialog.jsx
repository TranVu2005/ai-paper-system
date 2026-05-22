import { useEffect } from "react";
import { Button } from "@/components/ui/button";

export default function DeleteDocumentDialog({ open, loading, onClose, onConfirm }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold">Xóa tài liệu</h3>
        <p className="mt-2 text-sm text-zinc-600">Bạn có chắc muốn xóa tài liệu này khỏi phiên làm việc?</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={loading}>Hủy</Button>
          <Button onClick={onConfirm} disabled={loading} className="bg-red-600 text-white hover:bg-red-700">
            {loading ? "Đang xóa..." : "Xóa"}
          </Button>
        </div>
      </div>
    </div>
  );
}

