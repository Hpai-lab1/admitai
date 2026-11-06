// jest-dom's matchers for vitest 2, whose Assertion type lives in @vitest/expect
import type { TestingLibraryMatchers } from "@testing-library/jest-dom/matchers";

declare module "@vitest/expect" {
  interface Assertion<T = any> extends TestingLibraryMatchers<any, T> {}
}
