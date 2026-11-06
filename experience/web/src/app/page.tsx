import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-16">
      <h1 className="text-3xl font-bold">AdmitAI · Experience &amp; Operations</h1>
      <p className="mt-2 text-muted">Local development build of Module C.</p>
      <ul className="mt-8 space-y-3">
        <li>
          <Link className="btn-primary" href="/demo-site">
            Demo college website with the chat widget
          </Link>
        </li>
        <li>
          <Link className="btn-secondary" href="/console/login">
            Staff console
          </Link>
        </li>
      </ul>
    </main>
  );
}
