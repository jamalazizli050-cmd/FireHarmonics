import { zodResolver } from '@hookform/resolvers/zod';
import { CheckCircle2, ChevronDown, Loader2, Mail, MessageCircleMore, XCircle } from 'lucide-react';
import { motion } from 'motion/react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import teamIllustration from '../assets/derived/contact-team-illustration.png';
import { sendContactMessage, type ContactResult } from '../lib/contact';

const COUNTRY_CODES = [
  ['+994', 'Azerbaijan'],
  ['+1', 'United States / Canada'],
  ['+44', 'United Kingdom'],
  ['+49', 'Germany'],
  ['+33', 'France'],
  ['+90', 'Türkiye'],
  ['+7', 'Russia / Kazakhstan'],
  ['+995', 'Georgia'],
  ['+380', 'Ukraine'],
  ['+91', 'India'],
  ['+86', 'China'],
  ['+81', 'Japan'],
  ['+61', 'Australia'],
  ['+55', 'Brazil'],
  ['+27', 'South Africa'],
] as const;

const schema = z.object({
  firstName: z.string().trim().min(1, 'Enter your first name.').max(80, 'Use at most 80 characters.'),
  lastName: z.string().trim().min(1, 'Enter your last name.').max(80, 'Use at most 80 characters.'),
  email: z.string().trim().min(1, 'Enter your email address.').email('Enter a valid email address, like name@example.com.'),
  countryCode: z.string(),
  phone: z
    .string()
    .trim()
    .refine((v) => v === '' || /^[0-9()\-\s]{5,20}$/.test(v), 'Enter digits only (5–20), or leave the phone number empty.'),
  message: z
    .string()
    .trim()
    .min(10, 'Write at least 10 characters.')
    .max(4000, 'Use at most 4000 characters.'),
  website: z.string().optional(),
});
type FormValues = z.infer<typeof schema>;

/** Contact (mockup 04): white form card on the left, team illustration on the right. */
export default function ContactPage() {
  const [result, setResult] = useState<ContactResult | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: 'onTouched',
    defaultValues: { firstName: '', lastName: '', email: '', countryCode: '+994', phone: '', message: '', website: '' },
  });

  const onSubmit = async (v: FormValues) => {
    setResult(null);
    const r = await sendContactMessage({
      firstName: v.firstName,
      lastName: v.lastName,
      email: v.email,
      phone: v.phone ? `${v.countryCode} ${v.phone}` : undefined,
      message: v.message,
      website: v.website,
    });
    setResult(r);
    if (r.ok) reset(); // values are preserved on any failure
  };

  const err = (name: keyof FormValues) =>
    errors[name] ? (
      <p id={`err-${name}`} className="form-error" role="alert">
        {errors[name]?.message as string}
      </p>
    ) : null;
  const a11y = (name: keyof FormValues) => ({
    'aria-invalid': errors[name] ? true : undefined,
    'aria-describedby': errors[name] ? `err-${name}` : undefined,
  });

  return (
    <div className="contact">
      <Mail className="contact__icon contact__icon--mail-left" aria-hidden="true" strokeWidth={1.5} />
      <Mail className="contact__icon contact__icon--mail-right" aria-hidden="true" strokeWidth={1.2} />
      <span className="contact__bubble" aria-hidden="true">
        <MessageCircleMore strokeWidth={1.8} />
      </span>

      <motion.h1
        className="page-title contact__title"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.7 }}
      >
        Get in touch with our <span className="contact__underline">team</span>
      </motion.h1>

      <motion.form
        className="contact-form"
        onSubmit={handleSubmit(onSubmit)}
        noValidate
        aria-labelledby="contact-form-label"
        initial={{ opacity: 0, x: -24 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.7, delay: 0.1 }}
      >
        <h2 id="contact-form-label" className="visually-hidden">
          Contact form
        </h2>
        <div className="contact-form__row">
          <div className="contact-form__field">
            <label htmlFor="c-first" className="visually-hidden">
              First name
            </label>
            <input id="c-first" className="pill-input" placeholder="First name" autoComplete="given-name" {...a11y('firstName')} {...register('firstName')} />
            {err('firstName')}
          </div>
          <div className="contact-form__field">
            <label htmlFor="c-last" className="visually-hidden">
              Last name
            </label>
            <input id="c-last" className="pill-input" placeholder="Last name" autoComplete="family-name" {...a11y('lastName')} {...register('lastName')} />
            {err('lastName')}
          </div>
        </div>

        <div className="contact-form__field">
          <label htmlFor="c-email" className="visually-hidden">
            Email
          </label>
          <div className="pill-input pill-input--icon">
            <Mail aria-hidden="true" size={18} strokeWidth={1.5} />
            <input id="c-email" type="email" placeholder="Your email" autoComplete="email" {...a11y('email')} {...register('email')} />
          </div>
          {err('email')}
        </div>

        <div className="contact-form__field">
          <div className="pill-input pill-input--phone">
            <label htmlFor="c-cc" className="visually-hidden">
              Country calling code
            </label>
            <span className="cc-select">
              <select id="c-cc" {...register('countryCode')}>
                {COUNTRY_CODES.map(([code, country]) => (
                  <option key={code} value={code} aria-label={`${country} ${code}`}>
                    {code}
                  </option>
                ))}
              </select>
              <ChevronDown aria-hidden="true" size={18} />
            </span>
            <label htmlFor="c-phone" className="visually-hidden">
              Phone number (optional)
            </label>
            <input id="c-phone" type="tel" placeholder="Phone number" autoComplete="tel-national" {...a11y('phone')} {...register('phone')} />
          </div>
          {err('phone')}
        </div>

        <div className="contact-form__field">
          <label htmlFor="c-message" className="visually-hidden">
            Message
          </label>
          <textarea id="c-message" className="box-input" placeholder="How can we help you?" rows={6} {...a11y('message')} {...register('message')} />
          {err('message')}
        </div>

        {/* Honeypot: hidden from people and assistive technology; bots that fill it are ignored. */}
        <div className="hp-field" aria-hidden="true">
          <label htmlFor="c-website">Website</label>
          <input id="c-website" type="text" tabIndex={-1} autoComplete="off" {...register('website')} />
        </div>

        <button type="submit" className="contact-form__submit" disabled={isSubmitting}>
          {isSubmitting ? (
            <>
              <Loader2 className="spin" aria-hidden="true" size={18} /> Sending…
            </>
          ) : (
            'Submit'
          )}
        </button>

        <div aria-live="polite" className="contact-form__result">
          {result?.ok && (
            <p className="form-success">
              <CheckCircle2 aria-hidden="true" size={18} /> Thank you — your message was delivered to the team.
            </p>
          )}
          {result && !result.ok && (
            <p className="form-failure" role="alert">
              <XCircle aria-hidden="true" size={18} /> {result.message}
            </p>
          )}
        </div>
      </motion.form>

      <motion.img
        className="contact__illustration"
        src={teamIllustration}
        alt=""
        aria-hidden="true"
        initial={{ opacity: 0, x: 24 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.8, delay: 0.2 }}
      />
    </div>
  );
}
