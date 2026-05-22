import { Routes, Route, Navigate } from "react-router-dom";
import LoginPage from "./pages/Login";
import RegisterPage from "./pages/Register";
import UserHomePage from "./pages/Home";
import UploadPage from "./pages/Upload";
import LibraryPage from "./pages/Library";
import DocumentDetailPage from "./pages/DocumentDetail";
import SearchPage from "./pages/Search";
import ProfilePage from "./pages/Profile";
import AnalyticsPage from "./pages/Analytics";
import GoogleAuthCallbackPage from "./pages/GoogleAuthCallback";
import ForgotPasswordPage from "./pages/ForgotPassword";

import AdminDashboardPage from "./pages/AdminDashboardPage";
import AdminUsersPage from "./pages/AdminUsersPage";
import AdminDocumentsPage from "./pages/AdminDocumentsPage";

function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/auth/google/callback" element={<GoogleAuthCallbackPage />} />
      <Route path="/home" element={<UserHomePage />} />

      <Route path="/admin" element={<Navigate to="/admin/dashboard" replace />} />
      <Route path="/admin/dashboard" element={<AdminDashboardPage />} />
      <Route path="/admin/users" element={<AdminUsersPage />} />
      <Route path="/admin/documents" element={<AdminDocumentsPage />} />

      <Route path="/upload" element={<UploadPage />} />
      <Route path="/library" element={<LibraryPage />} />
      <Route path="/workspace/:workspaceId" element={<DocumentDetailPage />} />
      <Route path="/document/:id" element={<DocumentDetailPage />} />
      <Route path="/search" element={<SearchPage />} />
      <Route path="/analytics" element={<AnalyticsPage />} />
      <Route path="/profile" element={<ProfilePage />} />
    </Routes>
  );
}

export default App;
