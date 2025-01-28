
import xmlrpc.client 

class OProducts:
    def __init__(self, url, db, username, password):
        self.url = url
        self.db = db
        self.username = username
        self.password = password
        self.uid = self.authenticate()
        self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')
        self.category_dict = self.get_categories()

    def authenticate(self):
        common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
        return common.authenticate(self.db, self.username, self.password, {})

    def get_categories(self):
        categories = self.models.execute_kw(self.db, self.uid, self.password,
                                            'product.public.category', 'search_read',
                                            [[]],
                                            {'fields': ['id', 'name', 'parent_id']})
        return {cat['id']: {'name': cat['name'], 'parent_id': cat['parent_id'][0] if cat['parent_id'] else None} for cat in categories}

    def build_path(self, category_id):
        path = []
        while category_id:
            category = self.category_dict[category_id]
            path.insert(0, category['name'])
            category_id = category['parent_id']
        return '/'.join(path)

    def get_products_by_category(self, fullpath_filter):
        # products = self.models.execute_kw(self.db, self.uid, self.password,
        #                                   'product.template', 'search_read',
        #                                   [[]],
        #                                   {'fields': ['name', 'public_categ_ids', 'image_1920']})
        # Obtener 20 productos
        products = self.models.execute_kw(self.db, self.uid, self.password,
            'product.template', 'search_read',
            [[]],  # Filtro vacío para obtener todos los productos
            {'fields': ['name', 'public_categ_ids', 'image_1920'], 'limit': 1}#, 'limit': 20}  # 'limit' para restringir a 20 registros
        )

        category_paths = {cat_id: self.build_path(cat_id) for cat_id in self.category_dict}
        filtered_products = []

        for product in products:
            categ_ids = product['public_categ_ids']
            category_names = [category_paths[cid] for cid in categ_ids if cid in category_paths]

            for path in category_names:
                if fullpath_filter in path:
                    filtered_products.append({
                        'name': product['name'],
                        'category': path,
                        'image': product['image_1920']
                    })
                    # uno = True
                    # if (uno):
                    #     print(f"Nombre: {product['name']}")
                    #     print(f"categoria: {path}")
                    #     print(f"imagen: {product['image_1920']}")
                    #     uno = False
                #break
                
        return filtered_products
    
    def get_products_telas(self, item_name):
        # products = self.models.execute_kw(self.db, self.uid, self.password,
        #                                   'product.template', 'search_read',
        #                                   [[]],
        #                                   {'fields': ['name', 'public_categ_ids', 'image_1920']})
        # Obtener 20 productos
        products = self.models.execute_kw(self.db, self.uid, self.password,
            'product.template', 'search_read',
            [[]],  # Filtro vacío para obtener todos los productos
            {'fields': ['name', 'public_categ_ids', 'image_1920']}#, 'limit': 20}  # 'limit' para restringir a 20 registros
        )

        category_paths = {cat_id: self.build_path(cat_id) for cat_id in self.category_dict}
        filtered_blackout = []
        filtered_sheer = []
        rs = []
        path_filter = 'CORTINAS/SHADES/TELAS/' + item_name
        for product in products:
            categ_ids = product['public_categ_ids']
            category_names = [category_paths[cid] for cid in categ_ids if cid in category_paths]

            for path in category_names:
                if path_filter in path:
                    filtered_blackout.append({
                        'name': product['name'],
                        #'category': path,
                        'image': product['image_1920']
                    })
                # if 'CORTINAS/SHADES/TELAS/SHEER' in path:
                #     filtered_sheer.append({
                #         'name': product['name'],
                #         'category': path,
                #         'image': product['image_1920']
                #     })
                    # uno = True
                    # if (uno):
                    #     print(f"Nombre: {product['name']}")
                    #     print(f"categoria: {path}")
                    #     print(f"imagen: {product['image_1920']}")
                    #     uno = False
                #break
        # Crear un diccionario para almacenar las dos listas
        # rs = {
        #     'blackout': filtered_blackout,
        #     'sheer_elegance': filtered_sheer
        # }
        return filtered_blackout