type LoadingShellProps = {
  eyebrow: string
  title: string
  cards?: number
}

export function LoadingShell({ eyebrow, title, cards = 4 }: LoadingShellProps) {
  return (
    <section className="loading-shell" aria-label={title}>
      <div className="loading-shell__hero">
        <span className="page-card__eyebrow">{eyebrow}</span>
        <span className="skeleton skeleton--title" />
        <span className="skeleton skeleton--text" />
      </div>

      <div className="loading-shell__grid">
        {Array.from({ length: cards }).map((_, index) => (
          <article key={`${title}-${index}`} className="loading-shell__card">
            <span className="skeleton skeleton--eyebrow" />
            <span className="skeleton skeleton--headline" />
            <span className="skeleton skeleton--text" />
            <span className="skeleton skeleton--text short" />
          </article>
        ))}
      </div>
    </section>
  )
}
