export type Role = "super_admin" | "college_admin" | "admission_officer" | "faculty" | "marketing_manager";

export type NavItem = { href: string; label: string; roles: Role[] };

const QUEUE: Role[] = ["super_admin", "college_admin", "admission_officer", "faculty"];

// PRD 5.3 permission matrix, as screens
export const NAV: NavItem[] = [
  { href: "/console", label: "Queue", roles: QUEUE },
  {
    href: "/console/leads",
    label: "Leads",
    roles: ["super_admin", "college_admin", "admission_officer", "marketing_manager"],
  },
  { href: "/console/review", label: "Review answers", roles: QUEUE },
  { href: "/console/approvals", label: "Approvals", roles: QUEUE },
  { href: "/console/knowledge", label: "Knowledge", roles: QUEUE },
  {
    href: "/console/analytics",
    label: "Analytics",
    roles: ["super_admin", "college_admin", "admission_officer", "faculty", "marketing_manager"],
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
};
