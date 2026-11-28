const rawBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export const config = {
  apiBaseUrl: rawBase.replace(/\/+$/, ""),
  useMockApi: import.meta.env.VITE_USE_MOCK_API !== "false",
  maxUploadMb: Number(import.meta.env.VITE_MAX_UPLOAD_MB || 500)
};