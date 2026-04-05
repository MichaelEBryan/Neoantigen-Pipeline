import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind CSS classes with proper conflict resolution.
 * Combines clsx for conditional classes with tailwind-merge to handle
 * conflicting Tailwind utilities.
 *
 * @param inputs - Class names or conditional class objects
 * @returns Merged class string
 *
 * @example
 * cn('px-2 py-1', condition && 'px-4') // => 'py-1 px-4'
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
