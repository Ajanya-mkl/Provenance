import { MotionConfig } from "framer-motion";
import { lazy, Suspense } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router";

import { Shell } from "./components/Shell";
import { EmptyState, LoadingBlock, PageHeader } from "./components/ui";
import { DatasetProvider } from "./lib/dataset";
import { NamesProvider } from "./lib/names";
import { PendingProvider, usePending } from "./lib/pending";
import { SettingsProvider } from "./lib/settings";
import { ApprovalsPage } from "./pages/Approvals";
import { FlagPage } from "./pages/FlagDetail";
import { FlagsPage } from "./pages/Flags";
import { InvestigationPage, InvestigationsIndex } from "./pages/Investigation";
import { OverviewPage } from "./pages/Overview";
import { PipelinePage } from "./pages/Pipeline";

// The dataset flow is its own chunk: DataCo screens load without it.
const DatasetsPage = lazy(() => import("./pages/Datasets").then((m) => ({ default: m.DatasetsPage })));
const UploadPage = lazy(() => import("./pages/Datasets").then((m) => ({ default: m.UploadPage })));
const ProfilePage = lazy(() => import("./pages/DatasetProfile").then((m) => ({ default: m.ProfilePage })));
const MappingPage = lazy(() => import("./pages/MappingReview").then((m) => ({ default: m.MappingPage })));
const RunPage = lazy(() => import("./pages/DatasetRun").then((m) => ({ default: m.RunPage })));
const ResultsPage = lazy(() => import("./pages/DatasetResults").then((m) => ({ default: m.ResultsPage })));

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
      <Suspense fallback={<LoadingBlock rows={6} label="Loading page" />}>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/flags" element={<FlagsPage />} />
        <Route path="/flags/:eventId" element={<FlagPage />} />
        <Route path="/investigations" element={<InvestigationsIndex />} />
        <Route path="/investigations/:eventId" element={<InvestigationPage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/pipeline" element={<PipelinePage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/datasets/upload" element={<UploadPage />} />
        <Route path="/datasets/:id" element={<ProfilePage />} />
        <Route path="/datasets/:id/mapping" element={<MappingPage />} />
        <Route path="/datasets/:id/run" element={<RunPage />} />
        <Route path="/datasets/:id/results" element={<ResultsPage />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
      </Suspense>
    </Shell>
  );
}

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SettingsProvider>
      {/* Motion respects the OS reduced-motion setting everywhere. */}
      <MotionConfig reducedMotion="user">
        <PendingProvider>
          <DatasetProvider>
            <NamesProvider>{children}</NamesProvider>
          </DatasetProvider>
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
