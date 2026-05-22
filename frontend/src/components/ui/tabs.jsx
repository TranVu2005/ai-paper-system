import * as TabsPrimitive from "@radix-ui/react-tabs";

export function Tabs({ children, ...props }) {
  return <TabsPrimitive.Root {...props}>{children}</TabsPrimitive.Root>;
}

export function TabsList({ children, className = "", ...props }) {
  return (
    <TabsPrimitive.List
      className={`inline-grid gap-1 bg-slate-100 p-1 ${className}`}
      {...props}
    >
      {children}
    </TabsPrimitive.List>
  );
}

export function TabsTrigger({ children, className = "", ...props }) {
  return (
    <TabsPrimitive.Trigger
      className={`rounded-xl px-3 py-2 text-sm data-[state=active]:bg-white data-[state=active]:shadow ${className}`}
      {...props}
    >
      {children}
    </TabsPrimitive.Trigger>
  );
}