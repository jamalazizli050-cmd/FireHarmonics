import * as Accordion from '@radix-ui/react-accordion';
import { ChevronDown } from 'lucide-react';
import { motion } from 'motion/react';
import questionSymbol from '../assets/derived/faq-question-symbol.png';
import personWithLaptop from '../assets/derived/faq-person-with-laptop.png';
import { FAQ } from '../content/faq';

/** FAQ (mockup 05): two-column grid of rounded tiles; several answers can be open at once. */
export default function FaqPage() {
  return (
    <div className="faq">
      <img className="deco deco--faq-symbol" src={questionSymbol} alt="" aria-hidden="true" />
      <img className="deco deco--faq-person" src={personWithLaptop} alt="" aria-hidden="true" />
      <motion.h1
        className="page-title faq__title"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.7 }}
      >
        Frequently asked questions
      </motion.h1>
      <Accordion.Root type="multiple" className="faq__grid">
        {FAQ.map((item, i) => (
          <motion.div
            key={item.q}
            className="faq__cell"
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.1 + i * 0.05 }}
          >
            <Accordion.Item value={`q${i}`} className="faq-item">
              <Accordion.Header asChild>
                <h2 className="faq-item__heading">
                  <Accordion.Trigger className="faq-item__trigger">
                    <span>{item.q}</span>
                    <ChevronDown className="faq-item__chevron" aria-hidden="true" size={34} strokeWidth={1.6} />
                  </Accordion.Trigger>
                </h2>
              </Accordion.Header>
              <Accordion.Content className="faq-item__content">
                <div className="faq-item__answer">
                  {item.a.map((p) => (
                    <p key={p}>{p}</p>
                  ))}
                </div>
              </Accordion.Content>
            </Accordion.Item>
          </motion.div>
        ))}
      </Accordion.Root>
    </div>
  );
}
