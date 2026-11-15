import * as Tabs from '@radix-ui/react-tabs';
import { AnimatePresence, motion } from 'motion/react';
import { useState } from 'react';
import blueCloud from '../assets/derived/about-blue-cloud.png';
import whiteCloud from '../assets/derived/about-white-cloud.png';
import seatedPerson from '../assets/derived/about-seated-person.png';
import techIllustration from '../assets/derived/about-technology-illustration.png';
import blueCorner from '../assets/derived/about-blue-corner-graphic.png';
import { ABOUT_TABS } from '../content/about';

/** About (mockup 03): heading, rounded glass content box with four tabs, corner illustrations. */
export default function AboutPage() {
  const [tab, setTab] = useState(ABOUT_TABS[0].id);
  return (
    <div className="about">
      <div className="about__stage">
        <img className="deco deco--about-blue-cloud" src={blueCloud} alt="" aria-hidden="true" />
        <img className="deco deco--about-white-cloud" src={whiteCloud} alt="" aria-hidden="true" />
        <img className="deco deco--about-corner" src={blueCorner} alt="" aria-hidden="true" />
        <img className="deco deco--about-tech" src={techIllustration} alt="" aria-hidden="true" />
        <img className="deco deco--about-person" src={seatedPerson} alt="" aria-hidden="true" />

        <motion.h1
          className="page-title about__title"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7 }}
        >
          What is FireHarmonics?
        </motion.h1>

        <motion.div
          className="about__box glass-panel"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.15 }}
        >
          <Tabs.Root value={tab} onValueChange={setTab} className="about__tabs">
            <Tabs.List className="about__tablist" aria-label="About FireHarmonics">
              {ABOUT_TABS.map((t) => (
                <Tabs.Trigger key={t.id} value={t.id} className="about__tab">
                  {t.label}
                  {tab === t.id && <motion.span layoutId="about-tab-underline" className="about__tab-underline" />}
                </Tabs.Trigger>
              ))}
            </Tabs.List>
            <AnimatePresence mode="wait">
              {ABOUT_TABS.filter((t) => t.id === tab).map((t) => (
                <Tabs.Content key={t.id} value={t.id} forceMount asChild>
                  <motion.div
                    className="about__panel"
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    transition={{ duration: 0.3 }}
                  >
                    {t.body}
                  </motion.div>
                </Tabs.Content>
              ))}
            </AnimatePresence>
          </Tabs.Root>
        </motion.div>
      </div>
    </div>
  );
}
