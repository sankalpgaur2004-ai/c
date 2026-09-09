/**
 * ContextManager — Smart query history management
 * Tracks full context for each query (filters, sources, topics)
 * and provides context history for the backend analyzer
 */

export interface QueryContextElement {
  type: 'filter' | 'source' | 'metric' | 'dimension'
  value: string
  confidence: number // 0-1
}

export interface QueryHistoryEntry {
  id: string
  timestamp: number
  query: string
  sourceFilter: string
  elements: QueryContextElement[]
  sqlQuery?: string
  summary?: string
  // Add more metadata as needed
}

export interface ContextHistoryPayload {
  id: string
  query: string
  sourceFilter: string
  sqlQuery?: string
  summary?: string
}

export class ContextManager {
  private history: QueryHistoryEntry[] = []
  private maxHistorySize = 50

  /**
   * Add a completed query to history with its results
   */
  addQueryToHistory(
    query: string,
    sourceFilter: string,
    sqlQuery?: string,
    summary?: string
  ): void {
    const entry: QueryHistoryEntry = {
      id: `ctx-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      timestamp: Date.now(),
      query,
      sourceFilter,
      elements: this.extractContextElements(query),
      sqlQuery,
      summary,
    }

    this.history.push(entry)

    // Keep history bounded
    if (this.history.length > this.maxHistorySize) {
      this.history = this.history.slice(-this.maxHistorySize)
    }
  }

  /**
   * Extract context elements from a query (mirrors backend logic)
   */
  private extractContextElements(query: string): QueryContextElement[] {
    const elements: QueryContextElement[] = []
    const queryLower = query.toLowerCase()

    // Filter keywords
    const filterKeywords: Record<string, string[]> = {
      zone: ['zone', 'region', 'area', 'location', 'territory', 'district'],
      date: ['month', 'quarter', 'year', 'date', 'period', 'fiscal', 'week'],
      category: ['category', 'type', 'segment', 'class', 'group', 'division'],
      status: ['status', 'state', 'stage', 'phase'],
    }

    // Metric keywords
    const metricKeywords = ['count', 'sum', 'total', 'average', 'avg', 'min', 'max', 'percentage', 'growth', 'trend']

    // Dimension keywords
    const dimensionKeywords: Record<string, string[]> = {
      patient: ['patient', 'patients'],
      hcp: ['hcp', 'healthcare', 'provider', 'doctor', 'physician'],
      product: ['product', 'drug', 'medicine', 'medication'],
      sales: ['sales', 'revenue', 'income', 'earnings'],
    }

    // Extract filters
    for (const [filterType, keywords] of Object.entries(filterKeywords)) {
      for (const keyword of keywords) {
        if (queryLower.includes(keyword)) {
          const parts = queryLower.split(keyword)
          if (parts.length > 1) {
            const rest = parts[1].trim()
            const valueWords = rest.split(/\s+/).slice(0, 3)
            if (valueWords.length > 0) {
              const value = valueWords.join(' ').replace(/[,.!?]/g, '')
              const confidence = /^[a-z]/.test(value) ? 0.8 : 0.5
              elements.push({
                type: 'filter',
                value: `${filterType}:${value}`,
                confidence,
              })
            }
          }
          break
        }
      }
    }

    // Extract metrics
    for (const metric of metricKeywords) {
      if (queryLower.includes(metric)) {
        elements.push({
          type: 'metric',
          value: metric,
          confidence: 0.9,
        })
      }
    }

    // Extract dimensions
    for (const [dimType, keywords] of Object.entries(dimensionKeywords)) {
      for (const keyword of keywords) {
        if (queryLower.includes(keyword)) {
          elements.push({
            type: 'dimension',
            value: dimType,
            confidence: 0.85,
          })
          break
        }
      }
    }

    return elements
  }

  /**
   * Get context history for sending to backend
   * Includes recent queries with their metadata
   */
  getContextHistory(): ContextHistoryPayload[] {
    return this.history.map((entry) => ({
      id: entry.id,
      query: entry.query,
      sourceFilter: entry.sourceFilter,
      sqlQuery: entry.sqlQuery,
      summary: entry.summary,
    }))
  }

  /**
   * Get the last N entries from history
   */
  getRecentHistory(limit: number = 5): QueryHistoryEntry[] {
    return this.history.slice(-limit)
  }

  /**
   * Clear all history
   */
  clearHistory(): void {
    this.history = []
  }

  /**
   * Get full history (for debugging)
   */
  getFullHistory(): QueryHistoryEntry[] {
    return [...this.history]
  }

  /**
   * Get entries that match certain filters
   */
  findByFilter(filterType: string, filterValue: string): QueryHistoryEntry[] {
    return this.history.filter((entry) =>
      entry.elements.some((el) =>
        el.type === 'filter' &&
        el.value.toLowerCase().includes(filterValue.toLowerCase())
      )
    )
  }
}

// Export singleton instance for use across the app
export const contextManager = new ContextManager()
