import { NavLink } from 'react-router';

const LINKS = [
  { to: '/', label: 'Home' },
  { to: '/about', label: 'About' },
  { to: '/contact', label: 'Contact' },
  { to: '/faq', label: 'FAQ' },
];

export default function NavBar({ variant }: { variant: 'default' | 'globe' }) {
  return (
    <header className={`site-nav site-nav--${variant}`}>
      <nav aria-label="Main">
        <ul>
          {LINKS.map((l) => (
            <li key={l.to}>
              <NavLink to={l.to} end className={({ isActive }) => (isActive ? 'is-active' : undefined)}>
                {l.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </header>
  );
}
