import type { Programme } from "./programmes";

/** Made-up programmes of a demo college, shaped as GET /widget/programmes returns them, for tests (shaped as Module C's routes/programmes.py). */
export const SAMPLE: Programme[] = [
  {
    code: "BTECH-IN-COMPUTER-SCIENCE-AND-ENGINEERING",
    title: "B.Tech. in Computer Science and Engineering",
    level: "UG",
    faculty: "Faculty of Engineering and Technology",
    duration: "4 Years",
    intake: "60 Seats",
    fees: ["Total fee: ₹1,50,000 per year"],
    url: "https://demo-college.example/course/btech-in-computer-science-and-engineering",
    eligibility: true,
    careers: false,
  },
  {
    code: "BDES-FASHION-DESIGN",
    title: "B.Des. Fashion Design",
    level: "UG",
    faculty: "Faculty of Art and Design",
    duration: "4 Years",
    intake: "20 Seats",
    fees: ["Total fee: ₹1,20,000 per year"],
    url: "https://demo-college.example/course/bdes-fashion-design",
    eligibility: true,
    careers: false,
  },
  {
    code: "MBA",
    title: "Master's of Business Administration (MBA)",
    level: "PG",
    faculty: "Faculty of Management and Commerce",
    duration: "2 Years",
    intake: "",
    fees: [],
    url: "https://demo-college.example/course/mba",
    eligibility: false,
    careers: false,
  },
];
