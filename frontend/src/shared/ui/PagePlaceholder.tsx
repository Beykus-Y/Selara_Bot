type PagePlaceholderProps = {
  title: string
  description: string
  bullets: string[]
}

export function PagePlaceholder({ title, description, bullets }: PagePlaceholderProps) {
  return (
    <section className="page-card">
      <div className="page-card__header">
        <span className="page-card__eyebrow">В разработке</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>

      <div className="page-card__content">
        <h2>Что уже готово</h2>
        <ul>
          {bullets.map((bullet) => (
            <li key={bullet}>{bullet}</li>
          ))}
        </ul>
      </div>
    </section>
  )
}
