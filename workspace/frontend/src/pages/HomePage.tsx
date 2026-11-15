import { motion } from 'motion/react';
import { useState } from 'react';
import { useNavigate } from 'react-router';
import LocationSearch from '../components/ui/LocationSearch';
import type { Place } from '../lib/geocode';
import { useA11y } from '../state/a11y';
import { locationSearch } from '../state/location';

/**
 * Home (mockup 01): starfield, large decorative Earth (rendered by the persistent GlobeStage),
 * brand title, location search and subtitle. Selecting a place fades the text out while the same
 * Earth glides, shrinks and gains country borders to become the Interactive Globe.
 */
export default function HomePage() {
  const navigate = useNavigate();
  const { reduceMotion } = useA11y();
  const [leaving, setLeaving] = useState(false);

  const onSelect = (place: Place) => {
    setLeaving(true);
    const go = () => navigate(`/globe${locationSearch(place)}`, { state: { fromHome: true } });
    if (reduceMotion) go();
    else setTimeout(go, 380);
  };

  return (
    <motion.div
      className="home"
      animate={{ opacity: leaving ? 0 : 1, y: leaving ? -12 : 0 }}
      transition={{ duration: 0.38, ease: 'easeOut' }}
    >
      <motion.h1
        className="home__title"
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 1.1, delay: 0.7, ease: [0.22, 1, 0.36, 1] }}
      >
        FireHarmonics
      </motion.h1>
      <motion.div
        className="home__search"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.9, delay: 1.25 }}
      >
        <LocationSearch variant="home" onSelect={onSelect} />
      </motion.div>
      <motion.p
        className="home__subtitle"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 1.2, delay: 1.5 }}
      >
        Harmonization of MODIS and VIIRS Hot Spots
      </motion.p>
    </motion.div>
  );
}
