export function AdminPlaceholderPage({ title }: { title: string }) {
  return (
    <section className="admin-placeholder">
      <p className="admin-eyebrow">Selara Admin</p>
      <h1>{title}</h1>
      <p>Этот раздел появится на следующем этапе реализации панели.</p>
    </section>
  )
}
