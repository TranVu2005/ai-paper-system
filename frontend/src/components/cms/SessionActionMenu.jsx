import { Pencil, Trash2 } from "lucide-react";

export default function SessionActionMenu({ open, onRename, onDelete }) {
  if (!open) return null;
  return (
    <div className="absolute right-0 z-20 mt-1 w-44 rounded-md border border-zinc-200 bg-white p-1 shadow">
      <button
        type="button"
        onClick={onRename}
        className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-zinc-800 hover:bg-zinc-100"
      >
        <Pencil className="h-4 w-4" />
        Sửa tên
      </button>
      <button
        type="button"
        onClick={onDelete}
        className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50"
      >
        <Trash2 className="h-4 w-4" />
        Xóa phiên làm việc
      </button>
    </div>
  );
}

