type PanelGlyphKind =
  | 'chat'
  | 'docs'
  | 'gamepad'
  | 'settings'
  | 'spark'
  | 'telegram'
  | 'trophy'

type PanelGlyphProps = {
  kind: PanelGlyphKind
}

export function PanelGlyph({ kind }: PanelGlyphProps) {
  switch (kind) {
    case 'chat':
      return (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M5 7.25A3.25 3.25 0 0 1 8.25 4h7.5A3.25 3.25 0 0 1 19 7.25v5.5A3.25 3.25 0 0 1 15.75 16H11l-3.75 4v-4H8.25A3.25 3.25 0 0 1 5 12.75v-5.5Z" />
        </svg>
      )
    case 'docs':
      return (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M7.25 4h7.379a2 2 0 0 1 1.414.586l1.371 1.371A2 2 0 0 1 18 7.37v9.38A3.25 3.25 0 0 1 14.75 20h-7.5A3.25 3.25 0 0 1 4 16.75v-9.5A3.25 3.25 0 0 1 7.25 4Z" />
          <path d="M8 10h8M8 13.5h8M8 17h5" />
        </svg>
      )
    case 'gamepad':
      return (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M7.5 8h9a4 4 0 0 1 3.854 5.069l-.863 3.164A2.5 2.5 0 0 1 15.47 17.4l-2.05-1.64a2.25 2.25 0 0 0-2.81 0L8.56 17.4a2.5 2.5 0 0 1-4.021-1.167l-.863-3.164A4 4 0 0 1 7.5 8Z" />
          <path d="M8 11.5v3M6.5 13h3M15.5 12.25h.01M17.75 14.5h.01" />
        </svg>
      )
    case 'settings':
      return (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 4.5v2M12 17.5v2M4.5 12h2M17.5 12h2M6.697 6.697l1.414 1.414M15.889 15.889l1.414 1.414M17.303 6.697l-1.414 1.414M8.111 15.889l-1.414 1.414" />
          <circle cx="12" cy="12" r="3.5" />
        </svg>
      )
    case 'telegram':
      return (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M20 5 4.9 10.813a1 1 0 0 0 .066 1.89l3.41 1.063 1.168 3.77a1 1 0 0 0 1.796.252l2.053-2.71 3.73 2.729a1 1 0 0 0 1.572-.62L20.99 6.12A1 1 0 0 0 20 5Z" />
          <path d="m8.375 13.766 9.537-7.59" />
        </svg>
      )
    case 'trophy':
      return (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M8 4h8v2.25A4 4 0 0 1 12 10.25 4 4 0 0 1 8 6.25V4Z" />
          <path d="M8 5.5H5.75A1.75 1.75 0 0 0 4 7.25v.25A3.5 3.5 0 0 0 7.5 11h.3M16 5.5h2.25A1.75 1.75 0 0 1 20 7.25v.25A3.5 3.5 0 0 1 16.5 11h-.3M12 10.25v4.25M9 20h6M10 14.5h4V20h-4z" />
        </svg>
      )
    case 'spark':
    default:
      return (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Z" />
          <path d="m18.5 15 .8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2ZM5.5 14l.57 1.43L7.5 16l-1.43.57L5.5 18l-.57-1.43L3.5 16l1.43-.57L5.5 14Z" />
        </svg>
      )
  }
}
