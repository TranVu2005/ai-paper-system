import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { BookOpen, BrainCircuit, FileText } from "lucide-react";

import heroImage from "@/assets/hero.png";
import { Button } from "@/components/ui/button";

export function AuthHeroShell({
  brand = "PaperMind",
  title,
  subtitle,
  supporting,
  actionLabel,
  actionTo,
  children,
}) {
  return (
    <div className="relative min-h-screen overflow-hidden bg-[#08111c] text-white">
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{ backgroundImage: `url(${heroImage})` }}
      />
      <div className="absolute inset-0 bg-[#071019]/78" />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(74,222,128,0.18),_transparent_28%),radial-gradient(circle_at_bottom_right,_rgba(59,130,246,0.18),_transparent_34%)]" />
      <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.05)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.05)_1px,transparent_1px)] bg-[size:40px_40px] opacity-[0.08]" />

      <div className="relative flex min-h-screen flex-col">
        <header className="flex items-center justify-between px-6 py-6 lg:px-12">
          <Link to="/login" className="flex items-center gap-3 text-white">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-white/15 bg-white/8">
              <BookOpen className="h-5 w-5" />
            </div>
            <div>
              <div className="text-2xl font-black tracking-tight">{brand}</div>
              <div className="text-xs uppercase tracking-[0.26em] text-white/48">Nền tảng nghiên cứu</div>
            </div>
          </Link>
          {actionLabel && actionTo ? (
            <Link to={actionTo}>
              <Button className="rounded-xl border border-white/14 bg-white/10 px-5 py-3 text-sm font-semibold text-white hover:bg-white/16">
                {actionLabel}
              </Button>
            </Link>
          ) : null}
        </header>

        <main className="flex flex-1 items-center px-6 pb-12 pt-4 lg:px-12">
          <div className="mx-auto grid w-full max-w-6xl gap-10 lg:grid-cols-[1.05fr_0.95fr] lg:items-center">
            <motion.section
              initial={{ opacity: 0, y: 18 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.35 }}
              className="max-w-3xl text-center lg:text-left"
            >
              <p className="text-sm font-semibold uppercase tracking-[0.28em] text-emerald-200/78">
                Workspace cho tài liệu và AI
              </p>
              <h1 className="mt-6 text-5xl font-black leading-[0.95] text-white sm:text-6xl lg:text-7xl">
                {title}
              </h1>
              <p className="mx-auto mt-6 max-w-2xl text-lg leading-8 text-white/82 lg:mx-0">
                {subtitle}
              </p>
              {supporting ? (
                <p className="mx-auto mt-8 max-w-2xl text-base leading-7 text-white/68 lg:mx-0">
                  {supporting}
                </p>
              ) : null}

              <div className="mt-10 grid gap-3 sm:grid-cols-3">
                {[
                  ["Nhiều workspace", "Mỗi người dùng có thể mở nhiều phiên làm việc", BookOpen],
                  ["Nhiều nguồn", "Một notebook chứa được nhiều tài liệu", FileText],
                  ["AI có ngữ cảnh", "Tóm tắt, hỏi đáp và xử lý theo từng nguồn", BrainCircuit],
                ].map(([itemTitle, itemDesc, Icon]) => (
                  <div
                    key={itemTitle}
                    className="rounded-2xl border border-white/14 bg-white/[0.06] p-4 text-left backdrop-blur-sm"
                  >
                    <Icon className="h-5 w-5 text-emerald-200" />
                    <p className="mt-4 text-sm font-semibold text-white">{itemTitle}</p>
                    <p className="mt-2 text-sm leading-6 text-white/64">{itemDesc}</p>
                  </div>
                ))}
              </div>
            </motion.section>

            <motion.section
              initial={{ opacity: 0, y: 22 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.35, delay: 0.08 }}
              className="rounded-[32px] border border-white/14 bg-[#0a1420]/82 p-6 shadow-2xl backdrop-blur-xl sm:p-8"
            >
              {children}
            </motion.section>
          </div>
        </main>
      </div>
    </div>
  );
}
