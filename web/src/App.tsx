import { MotionConfig } from "framer-motion";
import { BrowserRouter, Link, Route, Routes } from "react-router";

import { Shell } from "./components/Shell";
import { EmptyState, PageHeader } from "./components/ui";
import { NamesProvider } from "./lib/names";
import { PendingProvider, usePending } from "./lib/pending";
import { SettingsProvider } from "./lib/settings";
import { ApprovalsPage } from "./pages/Approvals";
import { FlagPage } from "./pages/FlagDetail";
import { FlagsPage } from "./pages/Flags";
import { InvestigationPage, InvestigationsIndex } from "./pages/Investigation";
import { OverviewPage } from "./pages/Overview";
import { PipelinePage } from "./pages/Pipeline";

function NotFound() {
  return (
    <>
      <PageHeader title="Not found" />
      <EmptyState title="This page does not exist">
        <Link to="/" className="underline">
          Back to the overview
        </Link>
      </EmptyState>
    </>
  );
}

export function AppRoutes() {
  const { pending } = usePending();
  return (
    <Shell pending={pending}>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/flags" element={<FlagsPage />} />
        <Route path="/flags/:eventId" element={<FlagPage />} />
        <Route path="/investigations" element={<InvestigationsIndex />} />
        <Route path="/investigations/:eventId" element={<InvestigationPage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/pipeline" element={<PipelinePage />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Shell>
  );
}

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SettingsProvider>
      {/* Motion respects the OS reduced-motion setting everywhere. */}
      <MotionConfig reducedMotion="user">
        <PendingProvider>
          <NamesProvider>{children}</NamesProvider>
        </PendingProvider>
      </MotionConfig>
    </SettingsProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Providers>
        <AppRoutes />
      </Providers>
    </BrowserRouter>
  );
}
