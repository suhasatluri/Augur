/** @type {import('next').NextConfig} */
const nextConfig = {
  async redirects() {
    return [
      {
        source: '/about',
        destination: '/about.html',
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
