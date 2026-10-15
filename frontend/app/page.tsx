"use client";

import Shell from "@/components/Shell";
import { AppProvider } from "@/lib/state";

export default function Page() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
