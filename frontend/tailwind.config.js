/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
    './*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // ── Core palette ──────────────────────────────
        primary:   '#FFFFFF',
        secondary: {
          DEFAULT: '#E8EBF5',
          hover:   '#D8DBED',
          light:   '#F0F1F8',
          muted:   '#D4D8EB',
        },
        tertiary: {
          DEFAULT: '#8B1A2B',
          hover:   '#A31F32',
          light:   '#B02235',
          muted:   '#F5E6E8',
        },
        // ── Surfaces ──────────────────────────────────
        surface: {
          DEFAULT: '#F5F6FA',
          card:    '#FFFFFF',
          raised:  '#ECEEF5',
        },
        // ── Borders ───────────────────────────────────
        border: {
          DEFAULT: '#E2E6F0',
          strong:  '#C8CEDE',
        },
        // ── Text ──────────────────────────────────────
        ink: {
          DEFAULT: '#0F172A',
          muted:   '#4B5577',
          faint:   '#8B93AD',
          inverse: '#FFFFFF',
        },
      },
      fontFamily: {
        sans: ['DM Sans', 'Inter', 'system-ui', 'sans-serif'],
        serif: ['DM Serif Display', 'Georgia', 'serif'],
        mono: ['DM Mono', 'Courier New', 'monospace'],
      },
      borderRadius: {
        sm:  '6px',
        md:  '10px',
        lg:  '14px',
        xl:  '20px',
        '2xl': '28px',
      },
      boxShadow: {
        sm:   '0 1px 3px rgba(232,235,245,0.12)',
        md:   '0 4px 16px rgba(232,235,245,0.15)',
        lg:   '0 8px 32px rgba(232,235,245,0.20)',
        card: '0 2px 8px rgba(232,235,245,0.10)',
        navy: '0 4px 20px rgba(232,235,245,0.25)',
      },
      animation: {
        'fade-in':    'fadeIn 0.2s ease-out',
        'slide-in':   'slideIn 0.25s ease-out',
        'slide-up':   'slideUp 0.25s ease-out',
        'spin-slow':  'spin 1.5s linear infinite',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        slideIn: {
          from: { opacity: '0', transform: 'translateX(-12px)' },
          to:   { opacity: '1', transform: 'translateX(0)' },
        },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(12px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}
