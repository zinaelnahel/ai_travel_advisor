window.onload = function () {
  window.ui = SwaggerUIBundle({
    url: '/openapi.json',
    dom_id: '#swagger-ui',
    supportedSubmitMethods: ['get', 'post'],
    requestInterceptor: function (request) {
      Object.keys(request.headers).forEach(function (name) {
        if (name.toLowerCase() === 'x-travel-request') {
          delete request.headers[name];
        }
      });
      request.headers['X-Travel-Request'] = '1';
      request.credentials = 'same-origin';
      return request;
    }
  });
};
