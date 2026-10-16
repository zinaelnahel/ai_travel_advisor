window.onload = function () {
  window.ui = SwaggerUIBundle({
    url: '/openapi.json',
    dom_id: '#swagger-ui',
    supportedSubmitMethods: ['get', 'post'],
    requestInterceptor: function (request) {
      request.headers['X-Travel-Request'] = '1';
      request.credentials = 'same-origin';
      return request;
    }
  });
};
