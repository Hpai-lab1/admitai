export type Role =
  | "super_admin"
  | "college_admin"
  | "admission_officer"
  | "faculty"
  | "marketing_manager"
  // beyond PRD 5.2, for the way colleges staff admissions (README, "Staff roles")
  | "admission_counsellor"
  | "finance_officer"
  | "hostel_warden"
  | "management";

export type NavItem = { href: string; label: string; roles: Role[] };

// Roles limited to their departments (handoffs, feedback, approvals), and the admission desk.
export const DEPARTMENT_ROLES: Role[] = ["faculty", "finance_officer", "hostel_warden"];
export const ADMISSION_DESK: Role[] = ["admission_officer", "admission_counsellor"];

export function isDepartmentRole(role: Role): boolean {
  return DEPARTMENT_ROLES.includes(role);
}

/** Roles that see aggregates only and never work a queue. */
export function isAnalyticsOnly(role: Role): boolean {
  return role === "marketing_manager" || role === "management";
}

const QUEUE: Role[] = ["super_admin", "college_admin", ...ADMISSION_DESK, ...DEPARTMENT_ROLES];
// Counsellors suggest corrections but approve none.
const APPROVERS: Role[] = ["super_admin", "college_admin", "admission_officer", ...DEPARTMENT_ROLES];

// PRD 5.3 permission matrix, as screens
export const NAV: NavItem[] = [
  { href: "/console", label: "Queue", roles: QUEUE },
  {
    href: "/console/leads",
    label: "Leads",
    roles: ["super_admin", "college_admin", ...ADMISSION_DESK, "marketing_manager"],
  },
  { href: "/console/review", label: "Review answers", roles: QUEUE },
  { href: "/console/approvals", label: "Approvals", roles: APPROVERS },
  { href: "/console/knowledge", label: "Knowledge", roles: QUEUE },
  {
    href: "/console/analytics",
    label: "Analytics",
    roles: [
      "super_admin",
      "college_admin",
      ...ADMISSION_DESK,
      ...DEPARTMENT_ROLES,
      "marketing_manager",
      "management",
    ],
  },
  {
    href: "/console/preview",
    label: "Preview widget",
    roles: ["super_admin", "college_admin", "marketing_manager"],
  },
  { href: "/console/admin", label: "Admin", roles: ["college_admin"] },
  { href: "/console/platform", label: "Colleges", roles: ["super_admin"] },
];

export function navFor(role: Role): NavItem[] {
  return NAV.filter((n) => n.roles.includes(role));
}

export function homeFor(role: Role): string {
  return navFor(role)[0]?.href ?? "/console/analytics";
}

export const ROLE_LABEL: Record<Role, string> = {
  super_admin: "Platform admin",
  college_admin: "College admin",
  admission_officer: "Admission officer",
  faculty: "Faculty",
  marketing_manager: "Marketing",
  admission_counsellor: "Admission counsellor",
  finance_officer: "Finance officer",
  hostel_warden: "Hostel warden",
  management: "Management",
};

/** What each role can do, shown when a College Admin picks a role. */
export const ROLE_HELP: Record<Role, string> = {
  super_admin: "Runs the platform for every college.",
  college_admin: "Configures the college and approves every change.",
  admission_officer: "Works the admissions queue and leads; approves admission answers.",
  faculty: "Answers questions for their departments; approves those answers.",
  marketing_manager: "Leads and analytics; does not work the queue.",
  admission_counsellor: "Works the admissions queue and leads; suggests corrections, approves none.",
  finance_officer: "Fees, refunds and scholarships for their departments; approves those answers.",
  hostel_warden: "Hostel and accommodation questions for their departments; approves those answers.",
  management: "Analytics only: totals and trends, no personal details.",
};
