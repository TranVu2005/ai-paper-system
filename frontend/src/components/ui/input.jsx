export function Input({ className = "", ...props }) {
  return (
    <input
      className={`w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-slate-400 ${className}`}
      {...props}
    />
  );
}
