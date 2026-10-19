/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // 沿用 code_artifact.html 中的古典配色
        cinnabar: '#A83232',
        'cinnabar-light': '#C8553D',
        'cinnabar-dark': '#8B2828',
        celadon: '#6B8E7F',
        'celadon-dark': '#556F63',
        sandalwood: '#C9A97E',
        'sandalwood-dark': '#A88B5E',
        paper: '#F5F0E6',
        'paper-card': '#FBF9F5',
        'paper-base': '#F5F0E6',
        'paper-dark': '#EDE5D5',
        ink: '#2C2419',
        'ink-dark': '#1A1410',
        'ink-light': '#6B5D4F'
      },
      fontFamily: {
        serif: ['"Noto Serif SC"', 'serif'],
        calligraphy: ['"Ma Shan Zheng"', '"Noto Serif SC"', 'serif']
      }
    }
  },
  plugins: []
}
