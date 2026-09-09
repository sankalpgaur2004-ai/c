/**
 * powerbiApi.ts
 * -------------
 * Axios calls for all Power BI backend endpoints.
 */

import axios from "axios";

const BASE = import.meta.env.VITE_API_URL || "";

export interface EmbedTokenResponse {
  token:      string
  tokenId:    string
  expiration: string
  embedUrl:   string
}

export interface SchemaColumn {
  name:           string
  type?:          string
  sample_values?: any[]
  description?:   string
}

export interface SchemaTable {
  name:         string
  description?: string
  columns?:     SchemaColumn[]
}

export interface SchemaMeasure {
  name:                string
  description?:        string
  expression?:         string
  assigned_table?:     string
  page_group?:         string        // NEW
  dependent_measures?: string[]      // NEW
}

export interface SchemaRelationship {
  from_table:  string
  from_column: string
  to_table:    string
  to_column:   string
}

export interface EnrichedSchema {
  dataset?:       string
  workspace?:     string
  tables:         SchemaTable[]
  measures:       SchemaMeasure[]
  relationships:  SchemaRelationship[]
  pages?:         Record<string, string[]>  // NEW — page_name -> measure names
}

export interface ChatResponse {
  status:      string
  query_type?: string
  message?:    string
  filters?:    any[]
  dax_query?:  string
  needs_dax?:  boolean
  insight?:    string
  rows?:       any[]
}

export interface AnalyzePageResponse {
  status:   string
  insight?: string
  message?: string
}

// Active filter from slicer state
export interface ActiveFilter {
  table:  string
  column: string
  values: any[]
}

// Visual data from exportData()
export interface VisualData {
  title?: string
  type?:  string
  data?:  string   // CSV string
}

// Lightweight, unfiltered visual info — every visual on the page (title + type
// only, no exportData() call). Used to answer meta questions like "how many
// visuals are on this page" or "what are the titles of the visuals", which
// VisualData above can't answer accurately since it drops non-exportable
// visuals (images, textboxes, shapes) and any visual exportData() failed on.
export interface VisualInfo {
  title?: string
  type?:  string
}

// Report page from /pages endpoint
export interface ReportPage {
  name:        string
  displayName: string
  order:       number
}

export interface SchemaBuildProgress {
  status:  string
  step?:   string
  percent: number
  detail?: string
}

// ── Embed token ───────────────────────────────────────────────────────────────

export async function getEmbedToken(dashboardId: number): Promise<EmbedTokenResponse> {
  const res = await axios.get(
    `${BASE}/api/powerbi/${dashboardId}/embed-token`,
    { withCredentials: true }
  );
  return res.data;
}

// ── Schema ────────────────────────────────────────────────────────────────────

export async function getSchema(dashboardId: number): Promise<EnrichedSchema> {
  const res = await axios.get(
    `${BASE}/api/powerbi/${dashboardId}/schema`,
    { withCredentials: true }
  );
  return res.data;
}

export async function rebuildSchema(dashboardId: number): Promise<EnrichedSchema> {
  const res = await axios.post(
    `${BASE}/api/powerbi/${dashboardId}/schema/rebuild`,
    {},
    { withCredentials: true }
  );
  return res.data;
}

export async function getSchemaProgress(dashboardId: number): Promise<SchemaBuildProgress> {
  const res = await axios.get(
    `${BASE}/api/powerbi/${dashboardId}/schema/progress`,
    { withCredentials: true }
  );
  return res.data;
}

// ── Pages ─────────────────────────────────────────────────────────────────────

export async function getReportPages(dashboardId: number): Promise<ReportPage[]> {
  const res = await axios.get(
    `${BASE}/api/powerbi/${dashboardId}/pages`,
    { withCredentials: true }
  );
  return res.data;
}

// ── DAX ───────────────────────────────────────────────────────────────────────

export async function executeDax(dashboardId: number, query: string): Promise<any> {
  const res = await axios.post(
    `${BASE}/api/powerbi/${dashboardId}/dax`,
    { dashboard_id: dashboardId, query },
    { withCredentials: true }
  );
  return res.data;
}

// ── Chat (specific + descriptive questions) ───────────────────────────────────

export async function sendChat(
  dashboardId: number,
  message: string,
  conversationHistory: any[] = [],
  pageName?: string,
  activeFilters?: ActiveFilter[],
  pageVisualData?: VisualData[],
  pageVisualInventory?: VisualInfo[],
): Promise<ChatResponse> {
  const res = await axios.post(
    `${BASE}/api/powerbi/${dashboardId}/chat`,
    {
      dashboard_id:          dashboardId,
      message,
      conversation_history:  conversationHistory,
      page_name:             pageName    ?? null,
      active_filters:        activeFilters  ?? [],
      page_visual_data:      pageVisualData ?? [],
      page_visual_inventory: pageVisualInventory ?? [],
    },
    { withCredentials: true }
  );
  return res.data;
}

// ── Analyze page (descriptive summary) ───────────────────────────────────────

export async function analyzePageVisuals(
  dashboardId: number,
  question: string,
  pageName: string | null,
  pageVisualData: VisualData[],
  activeFilters: ActiveFilter[],
  conversationHistory: any[] = [],
  pageVisualInventory?: VisualInfo[],
): Promise<AnalyzePageResponse> {
  const res = await axios.post(
    `${BASE}/api/powerbi/${dashboardId}/analyze-page`,
    {
      question,
      page_name:             pageName,
      page_visual_data:      pageVisualData,
      page_visual_inventory: pageVisualInventory ?? [],
      active_filters:        activeFilters,
      conversation_history:  conversationHistory,
    },
    { withCredentials: true }
  );
  return res.data;
}