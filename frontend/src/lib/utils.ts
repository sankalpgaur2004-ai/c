import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Merge Tailwind classes safely — handles conflicts, conditionals */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}